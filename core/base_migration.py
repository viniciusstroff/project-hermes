"""
Compatibility facade for V1->V2 migration scripts.

New migration code should prefer receiving a MigrationContext by composition.
This class keeps the old inheritance API available while delegating runtime
responsibilities to smaller context components.
"""

import os
import shlex
import subprocess

import firebirdsql
import psycopg2

from core.context import DefaultMigrationContextFactory
from core.runner import MigrationRunner


class BaseMigration:
    def __init__(self, clientdir: str, context=None):
        self.context = context or DefaultMigrationContextFactory.from_clientdir(clientdir)
        self._clientdir = clientdir
        self.config = self.context.config
        self.artifacts = self.context.artifacts
        self._reference_maps = self.context._reference_maps

        self.pg_v1_conn = self.context.legacy_connections.get('pg_v1_conn')
        self.pg_v2_conn = self.context.legacy_connections.get('pg_v2_conn')
        self.fb_v1_conn = self.context.legacy_connections.get('fb_v1_conn')

        self.disable_triggers = ''
        self.enable_triggers = ''
        self.disable_indexes = ''
        self.enable_indexes = ''

    def __del__(self):
        try:
            if hasattr(self, 'context'):
                self.context.close()
        except Exception:
            pass

    @property
    def file(self):
        return self.context.writer.file

    @property
    def limit(self):
        return self.context.limit

    @limit.setter
    def limit(self, value):
        self.context.limit = value

    @property
    def debug_limit(self):
        return self.context.debug_limit

    @debug_limit.setter
    def debug_limit(self, value):
        self.context.debug_limit = value

    @property
    def _log_file(self):
        return self.context.logger._log_file

    @property
    def _tui_active(self):
        return self.context.progress.tui_active

    def enable_debug(self, quantidade=100):
        self.context.enable_debug(quantidade)

    @property
    def _engine(self):
        """Compatibility helper for the default PostgreSQL V1 source."""
        return self.context.engine('pg_v1')

    @property
    def _fb_engine(self):
        """Compatibility helper for the default Firebird V1 source."""
        return self.context.engine('fb_v1')

    def _log_table_progress(self, table: str, count: int, total: int = None, done: bool = False):
        self.context.progress.log_table_progress(table, count, total, done)

    def _setup_tui(self):
        self.context.progress._setup_tui()

    def _teardown_tui(self):
        self.context.progress.teardown_tui()

    def _footer_lines(self):
        return self.context.progress.footer_lines()

    def _render_progress_footer(self):
        self.context.progress.render_footer()

    def _reset_progress(self):
        self.context.progress.reset()

    def _finish_progress(self):
        self.context.progress.finish()

    def _open_sql_file(self, key: str):
        self.context.writer.open_sql_file(key)

    def write_sql(self, sql: str):
        self.context.writer.write_sql(sql)

    def _flush_writes(self):
        self.context.writer.flush()

    def _v1_cursor(self, factory=None):
        return self.context.sources['pg_v1'].cursor()

    def print_log(self, line):
        self.context.logger.print_log(line)

    def print_comment(self, line):
        self.context.writer.print_comment(line)

    def _write_manifest(self, status: str, error: str = None):
        self.context.write_manifest(status, error)

    def sql_value(self, value):
        return self.context.target.serialize_value(value)

    def load_reference_map(
        self,
        cache_key: str,
        sql: str,
        *,
        key_normalizer=None,
        value_normalizer=None,
        skip_null_keys: bool = True,
    ) -> dict:
        return self.context.load_reference_map(
            cache_key,
            sql,
            key_normalizer=key_normalizer,
            value_normalizer=value_normalizer,
            skip_null_keys=skip_null_keys,
        )

    def set_trigger_commands(self):
        self.print_log('Gerando comandos de triggers para tabelas')
        self.disable_triggers = self.context.metadata.disable_triggers_sql()
        self.enable_triggers = self.context.metadata.enable_triggers_sql()
        self.print_log('Comandos de trigger gerados')

    def disable_indexes_sql(self, indexes: list[str]) -> str:
        return self.context.metadata.disable_indexes_sql(indexes)

    def enable_indexes_sql(self, indexes: list[str]) -> str:
        return self.context.metadata.enable_indexes_sql(indexes)

    def set_index_commands_for(self, indexes: list[str]):
        self.disable_indexes = self.disable_indexes_sql(indexes)
        self.enable_indexes = self.enable_indexes_sql(indexes)

    def reset_sequence_sql(self, table: str, column: str = 'f_id') -> str:
        return self.context.metadata.reset_sequence_sql(table, column)

    def reset_sequences_sql(self, tables: list[str], column: str = 'f_id') -> str:
        return self.context.metadata.reset_sequences_sql(tables, column)

    def write_reset_sequences(self, tables: list[str], column: str = 'f_id'):
        self.write_sql(self.reset_sequences_sql(tables, column))

    def set_indexes_commands(self):
        """Override or call set_index_commands_for(indexes)."""
        pass

    def dump_ignoring_tables(self) -> list:
        """Return tables to exclude from pg_dump because they should be ignored."""
        return []

    def dump_non_existing_tables(self) -> list:
        """Return tables to exclude from pg_dump because they do not exist."""
        return []

    def reset_sequences(self):
        """Override or call write_reset_sequences(tables)."""
        pass

    def run_initial(self):
        """Override with SQL for 01_prepare_target.sql."""
        pass

    def run_inserts(self):
        """Override with SQL for 03_load_transformed_data.sql."""
        pass

    def dump_tables(self):
        exclude_tables = [*self.dump_ignoring_tables(), *self.dump_non_existing_tables()]

        command = (
            'pg_dump -h {host} -p {port} -U {user} -a -b --column-inserts -f {file} {name} '
            '-E utf-8 --disable-triggers {excludes}'
        ).format(
            host=self.config['PG_V1_HOST'],
            port=self.config['PG_V1_PORT'],
            user=self.config['PG_V1_USER'],
            file=self.artifacts.sql_path('dump_compatible_tables'),
            name=self.config['PG_V1_NAME'],
            excludes=''.join([f"-T '{t}' " for t in exclude_tables]),
        )

        os.environ['PGPASSWORD'] = self.config['PG_V1_PASS']
        self.print_log('Executando pg_dump de tabelas diferentes e com dados')

        if subprocess.call(shlex.split(command), shell=False) != 0:
            self._write_manifest('failed', 'Comando de dump falhou')
            raise Exception('Comando de dump falhou')

        self.file.seek(0, 2)
        self.print_log('Tabelas ignoradas: ' + ', '.join(exclude_tables))
        self.print_log('Dump finalizado')

    def run(self):
        MigrationRunner(self.context, self).run()
