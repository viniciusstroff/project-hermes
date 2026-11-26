"""Tasks of a migration script, declared with the ``task`` decorator.

A task is a method of the client class that runs as a whole: its comments,
its source reads and its migration units. Tasks with the same sequence do not
depend on the result of each other and may run at the same time. A task with
a higher sequence starts only after every task of a lower sequence finished.

    class Migracao(BaseMigration):
        @task('courses')
        def insert_courses(self): ...

        @task('students', sequence=2)
        def insert_students(self): ...

The merge order is the sequence and, within it, the declaration order in the
class. It is the order in which the commands of the tasks reach the phase.
"""

import itertools
from dataclasses import dataclass, replace

_declarations = itertools.count()

TASK_ATTRIBUTE = '__hermes_task__'


class TaskDeclarationError(TypeError):
    """A task declaration the runner cannot use, such as a repeated name."""


@dataclass(frozen=True)
class TaskSpec:
    """A declared task: name, sequence, declaration order and the method that runs it."""

    name: str
    sequence: int
    order: int
    method: str = None


def task(name=None, sequence: int = 1):
    """Declare a method of the migration script as a task.

    ``name`` defaults to the method name and must be unique in the class.
    ``sequence`` defaults to 1: tasks of the same sequence may run together.
    Also usable without parentheses: ``@task``.
    """

    if callable(name):
        return task()(name)
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise TaskDeclarationError(f'O nome da tarefa precisa ser um texto não vazio; recebeu {name!r}.')
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise TaskDeclarationError(f'A sequência da tarefa precisa ser um inteiro a partir de 1; recebeu {sequence!r}.')

    def decorate(function):
        setattr(function, TASK_ATTRIBUTE, TaskSpec(name or function.__name__, sequence, next(_declarations)))
        return function

    return decorate


def declared_tasks(cls) -> list:
    """Tasks of a class in merge order: sequence, then declaration order.

    A subclass that overrides a task method without the decorator removes
    the task. Repeated names are refused, naming both methods.
    """

    found = {}
    for klass in reversed(cls.__mro__):
        for attribute, value in vars(klass).items():
            spec = getattr(value, TASK_ATTRIBUTE, None)
            if isinstance(spec, TaskSpec):
                found[attribute] = replace(spec, method=attribute)
            elif attribute in found:
                del found[attribute]

    by_name = {}
    for spec in found.values():
        if spec.name in by_name:
            raise TaskDeclarationError(
                f"{cls.__name__}: a tarefa '{spec.name}' foi declarada em {by_name[spec.name].method}() "
                f'e em {spec.method}(). Dê um nome diferente a uma delas.'
            )
        by_name[spec.name] = spec
    return sorted(found.values(), key=lambda spec: (spec.sequence, spec.order))
