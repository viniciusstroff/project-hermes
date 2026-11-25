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
from core.operations import Clear, Comment, ResetIdentity, SuspendConstraints
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

    def __del__(self):
        try:
            if hasattr(self, 'context'):
                self.context.close()
        except Exception:
            pass

    @property
    def file(self):
        """Current artifact file, when the configured output writes files."""
        return getattr(self.context.output, 'file', None)

    @property
    def debug_limit(self):
        return self.context.debug_limit

    @debug_limit.setter
    def debug_limit(self, value):
        self.context.debug_limit = value

    def enable_debug(self, quantidade=100):
        self.context.enable_debug(quantidade)

    def print_log(self, line):
        self.context.logger.print_log(line)

    def _write_manifest(self, status: str, error: str = None):
        self.context.write_manifest(status, error)

    # Writing: operations rendered by the target adapter -----------------------

    def emit(self, operation):
        """Emit one operation of ``core.operations`` in the current phase."""
        self.context.emit(operation)

    def comment(self, text: str):
        self.emit(Comment(text))

    def clear(self, entities):
        """Remove the data of the entities, keeping their structure."""
        self.emit(Clear(entities))

    def suspend_constraints(self, entities=()):
        """Turn off triggers and checks of the entities (empty: all). The runner resumes them at the end."""
        self.emit(SuspendConstraints(entities))

    def reset_identity(self, entities, column: str = 'id'):
        """Move the identity of ``column`` past the highest loaded value, for each entity."""
        for entity in [entities] if isinstance(entities, str) else entities:
            self.emit(ResetIdentity(entity, column))

    def declare_reference_maps(self) -> list:
        """Override to declare the ReferenceMap list loaded before the first phase."""
        return []

    def reference_map(self, name: str):
        """Return a reference map declared in declare_reference_maps()."""
        return self.context.reference_map(name)

    def dump_ignoring_tables(self) -> list:
        """Return tables to exclude from pg_dump because they should be ignored."""
        return []

    def dump_non_existing_tables(self) -> list:
        """Return tables to exclude from pg_dump because they do not exist."""
        return []

    def reset_sequences(self):
        """Override to call reset_identity() in the finalize_target phase."""
        pass

    def run_initial(self):
        """Override with the operations of the prepare_target phase (clear, suspend_constraints)."""
        pass

    def run_inserts(self):
        """Override with the migrations of the load_transformed_data phase."""
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
            file=self.artifacts.command_path('dump_compatible_tables', 'sql'),
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
