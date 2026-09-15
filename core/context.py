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
from core.runtime import MigrationLogger, MigrationProgress, SqlArtifactWriter


@dataclass
class MigrationContext:
    """Composed runtime dependencies for a client migration."""

    clientdir: str
    config: dict
    artifacts: ArtifactManager
    sources: dict[str, Any]
    target: Any
    metadata: Any
    writer: SqlArtifactWriter
    logger: MigrationLogger
    progress: MigrationProgress
    legacy_connections: dict[str, Any] = field(default_factory=dict)
    limit: str = ''
    debug_limit: int | None = None
    _reference_maps: dict[str, dict] = field(default_factory=dict)
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
        )

    def engine(self, source_name: str = 'pg_v1') -> MigrationEngine:
        return MigrationEngine(
            self.sources[source_name],
            self.target,
            self.writer.write_sql,
            self.limit,
            progress_fn=self.progress.log_table_progress,
        )

    def load_reference_map(
        self,
        cache_key: str,
        sql: str,
        *,
        key_normalizer=None,
        value_normalizer=None,
        skip_null_keys: bool = True,
    ) -> dict:
        if cache_key not in self._reference_maps:
            self._reference_maps[cache_key] = self.metadata.load_reference_map(
                sql,
                key_normalizer=key_normalizer,
                value_normalizer=value_normalizer,
                skip_null_keys=skip_null_keys,
            )
        return self._reference_maps[cache_key]

    def close(self):
        if self._closed:
            return

        self.writer.close()
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
        writer = SqlArtifactWriter(artifacts)
        logger = MigrationLogger(artifacts)
        progress = MigrationProgress(logger)
        logger.progress = progress
        writer.open_sql_file('setup')

        artifacts.write_manifest(status='running', config=config, debug_limit=None)

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

            logger.print_log('----- Conectando BD Firebird V1 -----')
            fb_v1_conn = firebirdsql.connect(
                host=config['FB_V1_HOST'],
                port=config['FB_V1_PORT'],
                database=config['FB_V1_NAME'],
                user=config['FB_V1_USER'],
                password=config['FB_V1_PASS'],
                charset=config['FB_V1_CHARSET'])
        except Exception as exc:
            logger.print_log('[ERRO] ' + str(exc))
            artifacts.write_manifest(status='failed', config=config, debug_limit=None, error=str(exc))
            for connection in (pg_v1_conn, pg_v2_conn, fb_v1_conn):
                if connection is not None and hasattr(connection, 'close'):
                    connection.close()
            writer.close()
            logger.close()
            raise

        return MigrationContext(
            clientdir=clientdir,
            config=config,
            artifacts=artifacts,
            sources={
                'pg_v1': PostgresSourceAdapter(pg_v1_conn),
                'fb_v1': FirebirdSourceAdapter(fb_v1_conn),
            },
            target=PostgresTargetAdapter(),
            metadata=PostgresMetadataProvider(pg_v2_conn),
            writer=writer,
            logger=logger,
            progress=progress,
            legacy_connections={
                'pg_v1_conn': pg_v1_conn,
                'pg_v2_conn': pg_v2_conn,
                'fb_v1_conn': fb_v1_conn,
            },
        )
