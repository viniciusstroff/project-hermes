import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from dotenv import dotenv_values

from core.adapters.registry import connect_adapter
from core.apply.cli import session_opener, target_label
from core.artifacts import ArtifactManager
from core.checkpoint import Checkpoint, CheckpointedFileOutput, CheckpointError, check_resumable, current_resume
from core.connections import ConnectionConfigError, parse_connections
from core.metrics import GENERATION_REPORT, GenerationMetrics
from core.migration_engine import MigrationEngine
from core.operations import SuspendConstraints
from core.outputs import FileOutput, MirrorOutput, OutputAdapter
from core.outputs.direct import DirectDatabaseOutput
from core.parallel import TASK_OPERATIONS, ParallelSettings, TaskOperationError
from core.references import ReferenceMap, ReferenceMapError, build_reference_map, reference_pairs, reference_query
from core.runtime import MigrationLogger, MigrationProgress


FILE_OUTPUT = 'file'
DIRECT_OUTPUT = 'direct'
HYBRID_OUTPUT = 'hybrid'
OUTPUTS = (FILE_OUTPUT, DIRECT_OUTPUT, HYBRID_OUTPUT)


@dataclass(frozen=True)
class OutputSettings:
    """Output chosen on the command line.

    ``direct`` and ``hybrid`` run the commands on the target during the
    generation, so they need the mode (``apply`` or ``dry-run``). There is
    no default that writes to the target.
    """

    output: str = FILE_OUTPUT
    mode: str = None
    on_error: str = None

    def __post_init__(self):
        if self.output not in OUTPUTS:
            raise ValueError(f'output inválido: {self.output!r}. Use {", ".join(OUTPUTS)}.')
        if self.output != FILE_OUTPUT and self.mode is None:
            raise ValueError(f'o output {self.output} executa no destino e precisa do modo: apply ou dry-run.')


_OUTPUT_SETTINGS = ContextVar('hermes_output_settings', default=OutputSettings())
_PARALLEL_SETTINGS = ContextVar('hermes_parallel_settings', default=ParallelSettings())


@contextmanager
def output_settings(settings: OutputSettings):
    """Output used by the contexts built inside the block.

    ``Migracao.py`` builds the client inside it, so the client constructor
    (``super().__init__(_clientdir)``) does not change. Outside it, the
    factory uses the file output.
    """

    token = _OUTPUT_SETTINGS.set(settings)
    try:
        yield settings
    finally:
        _OUTPUT_SETTINGS.reset(token)


@contextmanager
def parallel_settings(settings: ParallelSettings):
    """Workers used by the contexts built inside the block. Outside it, one worker."""

    token = _PARALLEL_SETTINGS.set(settings)
    try:
        yield settings
    finally:
        _PARALLEL_SETTINGS.reset(token)


class SourceNotConfiguredError(LookupError):
    """Raised when a migration asks for a source the context does not have."""

    def __init__(self, source_name: str, sources: dict):
        available = ', '.join(sorted(sources)) or 'nenhuma'
        super().__init__(
            f"Origem '{source_name}' não configurada no .env do cliente. Origens disponíveis: {available}."
        )


@dataclass
class MigrationContext:
    """Composed runtime dependencies for a client migration."""

    clientdir: str
    config: dict
    artifacts: ArtifactManager
    sources: dict[str, Any]
    target: Any
    output: OutputAdapter
    logger: MigrationLogger
    progress: MigrationProgress
    connections: dict = field(default_factory=dict)
    metrics: GenerationMetrics = field(default_factory=GenerationMetrics)
    # Connection configs of the sources, so each worker opens its own connections.
    source_configs: dict = field(default_factory=dict)
    parallel: ParallelSettings = field(default_factory=ParallelSettings)
    target_config: Any = None
    # Checkpoint of the generation, with the file output.
    checkpoint: Any = None
    # Debug mode: row limit applied to every source query.
    debug_limit: int | None = None
    # Entities of each SuspendConstraints emitted; the runner resumes them at the end.
    suspended_constraints: list = field(default_factory=list)
    _loaded_reference_maps: dict = field(default_factory=dict)
    _summary_logged: bool = False
    # Name of the task running on this context with one worker.
    _running_task: str = None
    _warnings_logged: set = field(default_factory=set)
    _closed: bool = False

    def enable_debug(self, quantidade: int = 100):
        self.debug_limit = int(quantidade)
        self.write_manifest('running')

    def write_manifest(self, status: str, error: str = None):
        self.artifacts.write_manifest(
            status=status,
            connections=self.connections,
            debug_limit=self.debug_limit,
            error=error,
            output=self.output.describe(),
            parallel=self.parallel.describe(),
        )

    def source(self, source_name: str) -> MigrationEngine:
        """Engine that reads the source declared as SOURCE_<NAME>_* and writes to the target."""

        if source_name not in self.sources:
            raise SourceNotConfiguredError(source_name, self.sources)
        return MigrationEngine(
            self.sources[source_name],
            self.target,
            self.emit,
            self.debug_limit,
            progress_fn=self.progress.log_table_progress,
            warn_fn=self.warn_once,
            metrics=self.metrics,
            source_name=source_name,
        )

    def open_phase(self, phase: str):
        """Open a phase in the metrics and in the output."""

        self.metrics.open_phase(phase)
        self.output.open_phase(phase)

    def emit(self, operation):
        """Deliver one write operation to the output, the only write path of the run."""

        if self._running_task is not None and not isinstance(operation, TASK_OPERATIONS):
            raise TaskOperationError(
                f"A tarefa '{self._running_task}' emitiu {type(operation).__name__}, que pertence à fase de "
                'preparação ou de finalização. Uma tarefa só emite Insert, Comment e Native.'
            )
        if isinstance(operation, SuspendConstraints):
            self.suspended_constraints.append(operation.entities)
        self.output.write(operation)

    def warn_once(self, key: str, message: str):
        """Log a warning the first time a key is seen in this run."""

        if key in self._warnings_logged:
            return
        self._warnings_logged.add(key)
        self.logger.print_log('[AVISO] ' + message, level='warning')

    def load_reference_maps(self, declarations):
        """Load every declared reference map by reading the target adapter."""

        names = [declaration.name for declaration in declarations]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ReferenceMapError(f'Reference map declared more than once: {", ".join(repeated)}')

        for declaration in declarations:
            if not isinstance(declaration, ReferenceMap):
                raise ReferenceMapError(f'Invalid reference map declaration {declaration!r}: expected ReferenceMap')
            try:
                rows = self.target.read(reference_query(declaration))
                loaded = build_reference_map(declaration, reference_pairs(declaration, rows))
            except ReferenceMapError:
                raise
            except Exception as exc:
                raise ReferenceMapError(f"Reference map '{declaration.name}' failed to load: {exc}") from exc
            self._loaded_reference_maps[declaration.name] = loaded
            if len(loaded):
                self.logger.print_log(f'Mapa de referência {declaration.name}: {len(loaded)} chaves')
            else:
                self.warn_once(
                    f'empty-reference-map:{declaration.name}',
                    f'Mapa de referência {declaration.name} carregou vazio. Confira tabela, filtros e banco.',
                )

    def begin_checkpoint(self):
        """After target reads close: record the fingerprint, or check it and the source on resume."""

        if self.checkpoint is not None:
            self.checkpoint.begin(self)

    def write_generation_report(self, status: str, error: str = None):
        """Close the metrics, log the summary once and write relatorio-geracao.json."""

        if self.checkpoint is not None:
            self.checkpoint.finish(status, error)
            self.metrics.attempts = self.checkpoint.attempts
        self.metrics.finish(status, error)
        if not self._summary_logged:
            self._summary_logged = True
            for level, line in self.metrics.summary():
                self.logger.print_log(line, level=level)
        self.metrics.write_report(
            os.path.join(self.artifacts.run_dir, GENERATION_REPORT),
            debug_limit=self.debug_limit,
            artifacts=self.artifacts.artifacts(),
            run_dir=self.artifacts.run_dir,
        )

    def close_target_reads(self):
        """End target reads: from here on the generation does not query the target."""

        close_reads = getattr(self.target, 'close_reads', None)
        if close_reads is not None:
            close_reads()

    def reference_map(self, name: str):
        """Return a loaded reference map by its declared name."""

        if name not in self._loaded_reference_maps:
            raise ReferenceMapError(f"Reference map '{name}' was not declared in declare_reference_maps()")
        return self._loaded_reference_maps[name]

    def close(self):
        if self._closed:
            return

        self.output.close()
        if self.checkpoint is not None:
            self.checkpoint.release()
        _close_adapters([*self.sources.values(), self.target])
        self.logger.close()
        self._closed = True


class DefaultMigrationContextFactory:
    """Builds the default context from the named connections of the client .env."""

    @classmethod
    def from_clientdir(cls, clientdir: str) -> MigrationContext:
        config = dotenv_values(os.path.join(clientdir, '.env'))
        output_settings = _OUTPUT_SETTINGS.get()
        resume = current_resume()
        if resume is not None:
            # Checked before anything is written: a refused resume leaves the run as it was.
            if output_settings.output != FILE_OUTPUT:
                raise CheckpointError('A retomada só funciona com --output file.')
            clientpath = os.path.abspath(clientdir)
            check_resumable(os.path.join(
                os.path.dirname(clientpath), 'runs', os.path.basename(clientpath), resume.timestamp,
            ))
            artifacts = ArtifactManager(clientdir, timestamp=resume.timestamp)
        else:
            artifacts = ArtifactManager(clientdir)
        checkpoint = Checkpoint.open(artifacts.run_dir, resume) if output_settings.output == FILE_OUTPUT else None
        logger = MigrationLogger(artifacts)
        progress = MigrationProgress(logger)
        logger.progress = progress
        metrics = GenerationMetrics(logger.run_id, events=logger.event)
        logger.metrics = metrics
        if resume is not None:
            rerun = f', gerando de novo a partir da tarefa {resume.rerun}' if resume.rerun else ''
            logger.print_log(f'----- Retomando a execução {artifacts.timestamp}{rerun} -----')

        connections = {}
        opened = []
        output = None
        try:
            source_configs, target_config = parse_connections(config)
            parallel = _PARALLEL_SETTINGS.get()
            # Before any connection: the process mode is refused where fork is not safe.
            parallel.check([source.adapter for source in source_configs.values()] + [target_config.adapter])
            metrics.parallel = parallel.describe()
            connections = {
                'sources': {name: source.describe() for name, source in source_configs.items()},
                'target': target_config.describe(),
            }
            artifacts.write_manifest(status='running', connections=connections)
            logger.print_log('Artefatos da execução: ' + artifacts.run_dir)

            sources = {}
            for name, source_config in source_configs.items():
                logger.print_log(f'----- Conectando origem {name} ({source_config.adapter}) -----')
                sources[name] = connect_adapter(source_config, 'source')
                opened.append(sources[name])
                _record_server_version(connections['sources'][name], sources[name])

            logger.print_log(f'----- Conectando destino ({target_config.adapter}) -----')
            target = connect_adapter(target_config, 'target')
            opened.append(target)
            _record_server_version(connections['target'], target)
            _check_native_copies(source_configs, sources, target)

            # The artifact format, and so the file extension, comes from the target adapter.
            output = _build_output(output_settings, artifacts, target, target_config, logger, checkpoint)
            metrics.open_phase('setup')
            output.open_phase('setup')
        except Exception as exc:
            logger.print_log('[ERRO] ' + str(exc), level='error')
            artifacts.write_manifest(
                status='failed',
                connections=connections,
                error=str(exc),
                output=output.describe() if output is not None else None,
            )
            _close_adapters(opened)
            if output is not None:
                output.close()
            if checkpoint is not None:
                checkpoint.release()
            logger.close()
            raise

        return MigrationContext(
            clientdir=clientdir,
            config=config,
            artifacts=artifacts,
            sources=sources,
            target=target,
            output=output,
            logger=logger,
            progress=progress,
            connections=connections,
            metrics=metrics,
            source_configs=source_configs,
            parallel=parallel,
            target_config=target_config,
            checkpoint=checkpoint,
        )


def _build_output(settings: OutputSettings, artifacts, target, target_config, logger, checkpoint=None):
    if settings.output == FILE_OUTPUT:
        return CheckpointedFileOutput(artifacts, target, checkpoint) if checkpoint is not None else FileOutput(artifacts, target)
    direct = DirectDatabaseOutput(
        target,
        # Its own connection, opened when prepare_target opens.
        session_opener(type(target), target_config, waits_between_commands=True),
        artifacts.run_dir,
        settings.mode,
        settings.on_error,
        log=logger.print_log,
        target_label=target_label(target_config),
    )
    if settings.output == DIRECT_OUTPUT:
        return direct
    # The file first: it keeps the operation that failed on the target.
    return MirrorOutput(FileOutput(artifacts, target), direct)


def _check_native_copies(source_configs, sources, target):
    """A native copy writes commands of one target adapter: refuse it for another target."""

    for name, source in sources.items():
        native = getattr(source, 'native_copy', None)
        if native is not None and native.target_adapter != getattr(target, 'name', None):
            prefix = source_configs[name].prefix
            raise ConnectionConfigError(
                f'{prefix}_NATIVE_COPY={native.name} só serve para destino {native.target_adapter}; '
                f"o destino usa o adapter '{getattr(target, 'name', None)}'. Tire {prefix}_NATIVE_COPY."
            )


def _record_server_version(description: dict, adapter):
    version = getattr(adapter, 'server_version', None)
    if version:
        description['server_version'] = version


def _close_adapters(adapters):
    for adapter in adapters:
        close = getattr(adapter, 'close', None)
        if close is not None:
            close()
