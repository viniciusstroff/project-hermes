"""
Compatibility facade for V1->V2 migration scripts.

New migration code should prefer receiving a MigrationContext by composition.
This class keeps the old inheritance API available while delegating runtime
responsibilities to smaller context components.
"""

import os
import shlex
import subprocess

from core.connections import parse_connections
from core.context import DefaultMigrationContextFactory
from core.runner import MigrationRunner


class BaseMigration:
    # Named source read by pg_dump for the compatible tables. Until the
    # generic copy replaces pg_dump, it must be a PostgreSQL connection.
    dump_source = 'v1'

    def __init__(self, clientdir: str, context=None):
        self.context = context or DefaultMigrationContextFactory.from_clientdir(clientdir)
        self._clientdir = clientdir
        self.config = self.context.config
        self.artifacts = self.context.artifacts

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
        """Current SQL artifact file, when the configured output is file-based."""
        return getattr(self.context.output, 'file', None)

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

    def enable_debug(self, quantidade=100):
        self.context.enable_debug(quantidade)

    def _open_sql_file(self, key: str):
        self.context.output.open_phase(key)

    def write_sql(self, sql: str):
        self.context.output.write(sql)

    def _flush_writes(self):
        self.context.output.flush()

    def print_log(self, line):
        self.context.logger.print_log(line)

    def print_comment(self, line):
        self.context.output.comment(line)

    def _write_manifest(self, status: str, error: str = None):
        self.context.write_manifest(status, error)

    def sql_value(self, value):
        return self.context.target.serialize_value(value)

    def declare_reference_maps(self) -> list:
        """Override to declare the ReferenceMap list loaded before the first phase."""
        return []

    def reference_map(self, name: str):
        """Return a reference map declared in declare_reference_maps()."""
        return self.context.reference_map(name)

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
        source = self._dump_connection()

        command = (
            'pg_dump -h {host} -p {port} -U {user} -a -b --column-inserts -f {file} {name} '
            '-E utf-8 --disable-triggers {excludes}'
        ).format(
            host=source.get('host'),
            port=source.get('port', '5432'),
            user=source.get('user'),
            file=self.artifacts.sql_path('dump_compatible_tables'),
            name=source.get('name'),
            excludes=''.join([f"-T '{t}' " for t in exclude_tables]),
        )

        os.environ['PGPASSWORD'] = source.get('pass', '')
        self.print_log('Executando pg_dump de tabelas diferentes e com dados')

        if subprocess.call(shlex.split(command), shell=False) != 0:
            self._write_manifest('failed', 'Comando de dump falhou')
            raise Exception('Comando de dump falhou')

        self.print_log('Tabelas ignoradas: ' + ', '.join(exclude_tables))
        self.print_log('Dump finalizado')

    def _dump_connection(self):
        sources, _ = parse_connections(self.config)
        source = sources.get(self.dump_source)
        if source is None or source.adapter != 'postgres':
            raise ValueError(
                f"O pg_dump lê a origem '{self.dump_source}', que precisa ser declarada com "
                f"SOURCE_{self.dump_source.upper()}_ADAPTER=postgres"
            )
        source.require('host', 'name', 'user')
        return source

    def run(self):
        MigrationRunner(self.context, self).run()
