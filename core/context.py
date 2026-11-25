import os
from dataclasses import dataclass, field
from typing import Any

import firebirdsql
import psycopg2
from dotenv import dotenv_values

from core.adapters import FirebirdSourceAdapter, PostgresSourceAdapter, PostgresTargetAdapter
from core.artifacts import ArtifactManager
from core.metadata import PostgresMetadataProvider
from core.migration_engine import MigrationEngine
from core.outputs import OutputAdapter, SqlFileOutput
from core.references import ReferenceMap, ReferenceMapError, build_reference_map
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
    metadata: Any
    output: OutputAdapter
    logger: MigrationLogger
    progress: MigrationProgress
    legacy_connections: dict[str, Any] = field(default_factory=dict)
    limit: str = ''
    debug_limit: int | None = None
    _loaded_reference_maps: dict = field(default_factory=dict)
    _warnings_logged: set = field(default_factory=set)
    _closed: bool = False

    def enable_debug(self, quantidade: int = 100):
        self.limit = f'LIMIT {quantidade}'
        self.debug_limit = quantidade
        self.write_manifest('running')

    def write_manifest(self, status: str, error: str = None):
        self.artifacts.write_manifest(
            status=status,
            config=self.config,
            debug_limit=self.debug_limit,
            error=error,
            output=self.output.describe(),
        )

    def engine(self, source_name: str = 'pg_v1') -> MigrationEngine:
        if source_name not in self.sources:
            raise SourceNotConfiguredError(source_name, self.sources)
        return MigrationEngine(
            self.sources[source_name],
            self.target,
            self.output.write,
            self.limit,
            progress_fn=self.progress.log_table_progress,
            warn_fn=self.warn_once,
        )

    def warn_once(self, key: str, message: str):
        """Log a warning the first time a key is seen in this run."""

        if key in self._warnings_logged:
            return
        self._warnings_logged.add(key)
        self.logger.print_log('[AVISO] ' + message)

    def load_reference_maps(self, declarations):
        """Load every declared reference map through the metadata provider."""

        names = [declaration.name for declaration in declarations]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ReferenceMapError(f'Reference map declared more than once: {", ".join(repeated)}')

        for declaration in declarations:
            if not isinstance(declaration, ReferenceMap):
                raise ReferenceMapError(f'Invalid reference map declaration {declaration!r}: expected ReferenceMap')
            try:
                loaded = build_reference_map(declaration, self.metadata.load_reference_pairs(declaration))
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

    def reference_map(self, name: str):
        """Return a loaded reference map by its declared name."""

        if name not in self._loaded_reference_maps:
            raise ReferenceMapError(f"Reference map '{name}' was not declared in declare_reference_maps()")
        return self._loaded_reference_maps[name]

    def close(self):
        if self._closed:
            return

        self.output.close()
        seen = set()
        for connection in self.legacy_connections.values():
            if connection is None or id(connection) in seen:
                continue
            seen.add(id(connection))
            if hasattr(connection, 'close'):
                connection.close()
        self.logger.close()
        self._closed = True


class DefaultMigrationContextFactory:
    """Builds the default PostgreSQL/Firebird context from the existing .env keys."""

    @classmethod
    def from_clientdir(cls, clientdir: str) -> MigrationContext:
        config = dotenv_values(os.path.join(clientdir, '.env'))
        artifacts = ArtifactManager(clientdir)
        output = SqlFileOutput(artifacts)
        logger = MigrationLogger(artifacts)
        progress = MigrationProgress(logger)
        logger.progress = progress
        output.open_phase('setup')

        artifacts.write_manifest(status='running', config=config, debug_limit=None, output=output.describe())

        pg_v1_conn = None
        pg_v2_conn = None
        fb_v1_conn = None

        try:
            logger.print_log('Artefatos da execução: ' + artifacts.run_dir)
            logger.print_log('----- Conectando BD V1 -----')
            pg_v1_conn = psycopg2.connect(
                host=config['PG_V1_HOST'], port=config['PG_V1_PORT'],
                dbname=config['PG_V1_NAME'],
                user=config['PG_V1_USER'], password=config['PG_V1_PASS'])

            logger.print_log('----- Conectando BD V2 -----')
            pg_v2_conn = psycopg2.connect(
                host=config['PG_V2_HOST'], port=config['PG_V2_PORT'],
                dbname=config['PG_V2_NAME'],
                user=config['PG_V2_USER'], password=config['PG_V2_PASS'])

            if _firebird_configured(config):
                logger.print_log('----- Conectando BD Firebird V1 -----')
                fb_v1_conn = firebirdsql.connect(
                    host=config['FB_V1_HOST'],
                    port=config['FB_V1_PORT'],
                    database=config['FB_V1_NAME'],
                    user=config['FB_V1_USER'],
                    password=config['FB_V1_PASS'],
                    charset=config['FB_V1_CHARSET'])
            else:
                logger.print_log('----- Firebird V1 não configurado (FB_V1_HOST vazio) -----')
        except Exception as exc:
            logger.print_log('[ERRO] ' + str(exc))
            artifacts.write_manifest(
                status='failed',
                config=config,
                debug_limit=None,
                error=str(exc),
                output=output.describe(),
            )
            for connection in (pg_v1_conn, pg_v2_conn, fb_v1_conn):
                if connection is not None and hasattr(connection, 'close'):
                    connection.close()
            output.close()
            logger.close()
            raise

        return MigrationContext(
            clientdir=clientdir,
            config=config,
            artifacts=artifacts,
            sources=_sources(pg_v1_conn, fb_v1_conn),
            target=PostgresTargetAdapter(),
            metadata=PostgresMetadataProvider(pg_v2_conn),
            output=output,
            logger=logger,
            progress=progress,
            legacy_connections={
                'pg_v1_conn': pg_v1_conn,
                'pg_v2_conn': pg_v2_conn,
                'fb_v1_conn': fb_v1_conn,
            },
        )


def _firebird_configured(config: dict) -> bool:
    return bool((config.get('FB_V1_HOST') or '').strip())


def _sources(pg_v1_conn, fb_v1_conn) -> dict:
    sources = {'pg_v1': PostgresSourceAdapter(pg_v1_conn)}
    if fb_v1_conn is not None:
        sources['fb_v1'] = FirebirdSourceAdapter(fb_v1_conn)
    return sources
