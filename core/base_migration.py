"""
Compatibility facade for V1->V2 migration scripts.

New migration code should prefer receiving a MigrationContext by composition.
This class keeps the old inheritance API available while delegating runtime
responsibilities to smaller context components.
"""

import hashlib
import inspect

from core.context import DefaultMigrationContextFactory
from core.operations import Clear, Comment, ResetIdentity, SuspendConstraints
from core.parallel import Task, current_task_context, run_task_list
from core.runner import MigrationRunner
from core.tasks import declared_tasks


class BaseMigration:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # A repeated task name is refused when the client class is defined.
        declared_tasks(cls)

    def __init__(self, clientdir: str, context=None):
        self.context = context or DefaultMigrationContextFactory.from_clientdir(clientdir)
        self._clientdir = clientdir
        self.config = self.context.config
        self.artifacts = self.context.artifacts

    def __del__(self):
        try:
            if '_context' in self.__dict__:
                self._context.close()
        except Exception:
            pass

    @property
    def context(self):
        """Context of the run, or of the running task inside a worker."""

        task_context = current_task_context()
        if task_context is not None and task_context.parent is self._context:
            return task_context
        return self._context

    @context.setter
    def context(self, value):
        self._context = value

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
        """Run the tasks declared with @task. Override to load the phase another way."""
        self.run_tasks()

    def run_tasks(self):
        """Run the tasks declared with @task, in merge order, with the workers of the run."""

        tasks = [
            Task(
                spec.name,
                spec.sequence,
                lambda _context, method=spec.method: getattr(self, method)(),
                code_hash=_code_hash(getattr(type(self), spec.method)),
            )
            for spec in declared_tasks(type(self))
        ]
        run_task_list(self._context, tasks)

    def run(self):
        MigrationRunner(self._context, self).run()


def _code_hash(function) -> str:
    """Hash of the source of a task method, so a resume warns when a finished task changed."""

    try:
        source = inspect.getsource(function)
    except (OSError, TypeError):
        return None
    return hashlib.sha256(source.encode('utf-8')).hexdigest()[:16]
