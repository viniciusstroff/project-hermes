"""Entities that pass from the source to the target without transformation.

The migration lists them in ``compatible_entities()``. The runner copies them
in the ``copy_compatible_entities`` phase: each entity is read from its named
source and every row becomes one ``Insert`` in the target, under the same name
or under ``target``. Any pair of adapters works.

A source adapter may offer a native copy (``pg_dump`` in the PostgreSQL
adapter, enabled by ``SOURCE_<NAME>_NATIVE_COPY``). It covers the entities that
keep their name, outside debug mode. Renamed entities, and every entity in
debug mode, go through the row by row copy, which honors the debug limit.
"""

import os
import time
from dataclasses import dataclass

from core.parallel import Task, run_task_list
from core.queries import SourceQuery


@dataclass(frozen=True)
class CopyEntity:
    """One entity copied as it is: ``CopyEntity('public.holidays', source='v1')``."""

    entity: str
    source: str
    target: str = None
    order_by: tuple = ()

    def __post_init__(self):
        if not isinstance(self.entity, str) or not self.entity.strip():
            raise TypeError(f'CopyEntity precisa de uma entidade; recebeu {self.entity!r}.')
        if not isinstance(self.source, str) or not self.source:
            raise TypeError(f"CopyEntity('{self.entity}') precisa do nome da origem, como source='v1'.")
        order_by = (self.order_by,) if isinstance(self.order_by, str) else tuple(self.order_by or ())
        object.__setattr__(self, 'order_by', order_by)

    @property
    def target_entity(self) -> str:
        return self.target or self.entity

    @property
    def renamed(self) -> bool:
        return self.target_entity != self.entity

    def run(self, engine):
        """Copy row by row: one Insert per source row, with the debug limit of the engine."""

        query = SourceQuery(self.entity, order_by=self.order_by)
        label = self.target_entity
        with engine.unit(label) as unit:
            total = engine.total_rows(query)
            engine.report_progress(label, 0, total)

            processed = 0
            for row in engine.read(query):
                unit.row_read()
                engine.insert(label, row.items())
                processed += 1
                if processed % engine.progress_every == 0:
                    engine.report_progress(label, processed, total)

            engine.report_progress(label, processed, total, done=True)


def copy_compatible_entities(context, entities):
    """Copy every listed entity into the current phase of the output.

    Each entity is a task ``copy:<entity>`` and each native copy a task, so a
    resume skips what was copied. With more than one worker they run at the
    same time, and the dump file of a native copy is its segment. The merge
    order is the one of the sequential copy: per source, the row by row
    copies, then the native copy.
    """

    entities = list(entities)
    for entity in entities:
        if not isinstance(entity, CopyEntity):
            raise TypeError(f'compatible_entities() deve devolver CopyEntity; recebeu {entity!r}.')

    run_task_list(context, _copy_tasks(_plan(context, entities)))


def _plan(context, entities):
    """Per source, in listed order: row by row entities, native copy and its entities."""

    plan = []
    for source_name in dict.fromkeys(entity.source for entity in entities):
        group = [entity for entity in entities if entity.source == source_name]
        engine = context.source(source_name)
        native = getattr(engine.source_adapter, 'native_copy', None)
        native_entities = []
        if native is not None:
            if context.debug_limit is not None:
                context.logger.print_log(
                    f'Modo debug: a origem {source_name} copia linha a linha, sem {native.name}, para respeitar o limite.'
                )
            else:
                native_entities = [entity for entity in group if not entity.renamed]
                for entity in group:
                    if entity.renamed:
                        context.logger.print_log(
                            f'{entity.entity} muda de nome para {entity.target_entity}: '
                            f'vai pela cópia linha a linha, não pelo {native.name}.'
                        )
        rows = [entity for entity in group if entity not in native_entities]
        plan.append((source_name, rows, native, native_entities))
    return plan


def _copy_tasks(plan) -> list:
    tasks = []
    for source_name, rows, native, native_entities in plan:
        for entity in rows:
            tasks.append(Task(
                f'copy:{entity.target_entity}', 1, lambda context, entity=entity: entity.run(context.source(entity.source)),
            ))
        if native_entities:
            # Last in the phase: a native copy may change session settings,
            # such as the search_path that pg_dump empties.
            tasks.append(Task(
                f'copy:{source_name}:{native.name}',
                1,
                lambda context, source_name=source_name, native=native, entities=native_entities: (
                    _native_copy_task(context, source_name, native, entities)
                    if hasattr(context, 'segment_path') else _native_copy(context, source_name, native, entities)
                ),
                writes_segment=True,
            ))
    return tasks


def _native_copy_task(task_context, source_name, native, entities):
    """Native copy in a worker: the dump file is the segment of the task."""

    names = [entity.entity for entity in entities]
    task_context.logger.print_log(f'Copiando com {native.name} da origem {source_name}: {", ".join(names)}')
    started = time.monotonic()
    native.copy(names, task_context.segment_path)
    task_context.record_native_copy(
        source_name, native.name, names, time.monotonic() - started, os.path.getsize(task_context.segment_path),
    )


def _native_copy(context, source_name, native, entities):
    names = [entity.entity for entity in entities]
    context.logger.print_log(f'Copiando com {native.name} da origem {source_name}: {", ".join(names)}')
    path = os.path.join(context.artifacts.run_dir, f'{source_name}.{native.name}.tmp')
    started = time.monotonic()
    native.copy(names, path)
    size = os.path.getsize(path)
    context.output.include_file(path)
    os.remove(path)
    metrics = getattr(context, 'metrics', None)
    if metrics is not None:
        metrics.native_copy(source_name, native.name, names, time.monotonic() - started, size)
    context.logger.print_log(f'Cópia com {native.name} da origem {source_name} incluída')
