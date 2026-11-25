"""
Compatibility facade for V1->V2 migration scripts.

New migration code should prefer receiving a MigrationContext by composition.
This class keeps the old inheritance API available while delegating runtime
responsibilities to smaller context components.
"""

from core.context import DefaultMigrationContextFactory
from core.operations import Clear, Comment, ResetIdentity, SuspendConstraints
from core.runner import MigrationRunner


class BaseMigration:
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

    def compatible_entities(self) -> list:
        """Override with the CopyEntity list copied as it is in the copy_compatible_entities phase."""
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

    def run(self):
        MigrationRunner(self.context, self).run()
