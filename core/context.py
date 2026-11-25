import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import dotenv_values

from core.adapters.registry import connect_adapter
from core.artifacts import ArtifactManager
from core.connections import ConnectionConfigError, parse_connections
from core.migration_engine import MigrationEngine
from core.operations import SuspendConstraints
from core.outputs import FileOutput, OutputAdapter
from core.references import ReferenceMap, ReferenceMapError, build_reference_map, reference_pairs, reference_query
from core.runtime import MigrationLogger, MigrationProgress


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
    # Debug mode: row limit applied to every source query.
    debug_limit: int | None = None
    # Entities of each SuspendConstraints emitted; the runner resumes them at the end.
    suspended_constraints: list = field(default_factory=list)
    _loaded_reference_maps: dict = field(default_factory=dict)
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
        )

    def emit(self, operation):
        """Deliver one write operation to the output, the only write path of the run."""

        if isinstance(operation, SuspendConstraints):
            self.suspended_constraints.append(operation.entities)
        self.output.write(operation)

    def warn_once(self, key: str, message: str):
        """Log a warning the first time a key is seen in this run."""

        if key in self._warnings_logged:
            return
        self._warnings_logged.add(key)
        self.logger.print_log('[AVISO] ' + message)

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
        _close_adapters([*self.sources.values(), self.target])
        self.logger.close()
        self._closed = True


class DefaultMigrationContextFactory:
    """Builds the default context from the named connections of the client .env."""

    @classmethod
    def from_clientdir(cls, clientdir: str) -> MigrationContext:
        config = dotenv_values(os.path.join(clientdir, '.env'))
        artifacts = ArtifactManager(clientdir)
        logger = MigrationLogger(artifacts)
        progress = MigrationProgress(logger)
        logger.progress = progress

        connections = {}
        opened = []
        output = None
        try:
            source_configs, target_config = parse_connections(config)
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
            output = FileOutput(artifacts, target)
            output.open_phase('setup')
        except Exception as exc:
            logger.print_log('[ERRO] ' + str(exc))
            artifacts.write_manifest(
                status='failed',
                connections=connections,
                error=str(exc),
                output=output.describe() if output is not None else None,
            )
            _close_adapters(opened)
            if output is not None:
                output.close()
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
        )


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
