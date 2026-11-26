"""Metrics of a generation run: units, phases, data losses and the report.

The context owns one ``GenerationMetrics``. Each migration runs inside a unit
(``engine.unit(label)``), which counts the rows read from the source and the
inserts emitted per target entity. A strategy that cannot keep a source value
returns a ``LostValue``. The engine records it in the open unit and writes its
value. At the end of the run the context logs a summary and writes
``relatorio-geracao.json`` at the root of the run directory.

Nothing here knows a database: units count rows and ``Insert`` operations.
"""

import datetime
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass

GENERATION_REPORT = 'relatorio-geracao.json'

MAX_SAMPLES = 20
SAMPLE_LENGTH = 100
SUMMARY_SAMPLES = 5

DONE = 'done'
INTERRUPTED = 'interrupted'
RUNNING = 'running'

LOOKUP_MISS = 'lookup_miss'
INVALID_DATE = 'invalid_date'
EMAIL_NOT_FOUND = 'email_not_found'


@dataclass(frozen=True)
class LostValue:
    """Value a strategy writes in place of a source value it could not keep.

    ``value`` goes to the target as usual. ``reason`` names why the source
    value was lost, ``sample`` is the original value for the report and
    ``reference_map`` the name of the map a lookup missed, when it has one.
    """

    value: object
    reason: str
    sample: object
    reference_map: str = None


def is_blank(value) -> bool:
    """Null or empty source value: the source had no data to lose."""

    return value is None or str(value).strip() == ''


class LossGroup:
    """Losses of one column for one reason, with a bounded set of samples."""

    def __init__(self, entity: str, column: str, strategy: str, reason: str, reference_map: str = None):
        self.entity = entity
        self.column = column
        self.strategy = strategy
        self.reason = reason
        self.reference_map = reference_map
        self.count = 0
        self.samples = []

    def add(self, sample):
        self.count += 1
        if len(self.samples) < MAX_SAMPLES:
            text = str(sample)[:SAMPLE_LENGTH]
            if text not in self.samples:
                self.samples.append(text)

    def describe(self) -> str:
        """Readable line for the log, with up to five samples."""

        examples = ', '.join(repr(sample) for sample in self.samples[:SUMMARY_SAMPLES])
        suffix = f' (ex.: {examples})' if examples else ''
        return f'{self.entity}.{self.column}: {_loss_text(self)}{suffix}'

    def as_dict(self) -> dict:
        return {
            'entity': self.entity,
            'column': self.column,
            'strategy': self.strategy,
            'reason': self.reason,
            'reference_map': self.reference_map,
            'count': self.count,
            'samples': list(self.samples),
        }


def _loss_text(group: LossGroup) -> str:
    if group.reason == LOOKUP_MISS:
        where = f'no mapa {group.reference_map}' if group.reference_map else f'no dicionário do {group.strategy}'
        return f'{group.count} valores sem correspondência {where}'
    if group.reason == INVALID_DATE:
        return f'{group.count} datas inválidas'
    if group.reason == EMAIL_NOT_FOUND:
        return f'{group.count} textos sem e-mail'
    return f'{group.count} valores perdidos ({group.reason}) por {group.strategy}'


class Unit:
    """One migration run: rows read, inserts per target entity and losses."""

    def __init__(self, label: str, source: str, phase: str, clock, now):
        self.label = label
        self.source = source
        self.phase = phase
        self.state = RUNNING
        self.rows_read = 0
        self.inserts = {}
        self.losses = {}
        # Source counts of the unit, so a resume can check that the source did not change.
        self.counts = []
        self.started_at = now()
        self.finished_at = None
        self.duration_seconds = None
        self._started = clock()

    def row_read(self):
        self.rows_read += 1

    def inserted(self, entity: str):
        self.inserts[entity] = self.inserts.get(entity, 0) + 1

    def lost(self, entity: str, column: str, strategy: str, lost: LostValue):
        key = (entity, column, strategy, lost.reason)
        group = self.losses.get(key)
        if group is None:
            group = self.losses[key] = LossGroup(entity, column, strategy, lost.reason, lost.reference_map)
        group.add(lost.sample)

    @property
    def insert_count(self) -> int:
        return sum(self.inserts.values())

    def close(self, state: str, clock, now):
        self.state = state
        self.finished_at = now()
        self.duration_seconds = clock() - self._started

    @classmethod
    def from_dict(cls, data: dict) -> 'Unit':
        """Rebuild a finished unit sent by a worker as ``as_dict()``."""

        unit = cls.__new__(cls)
        unit.label = data['label']
        unit.source = data.get('source')
        unit.phase = data.get('phase')
        unit.state = data['state']
        unit.rows_read = data['rows_read']
        unit.inserts = dict(data['inserts'])
        unit.counts = list(data.get('counts', ()))
        unit.losses = {}
        for loss in data.get('losses', ()):
            group = LossGroup(loss['entity'], loss['column'], loss['strategy'], loss['reason'], loss['reference_map'])
            group.count = loss['count']
            group.samples = list(loss['samples'])
            unit.losses[(group.entity, group.column, group.strategy, group.reason)] = group
        unit.started_at = _parse_timestamp(data.get('started_at'))
        unit.finished_at = _parse_timestamp(data.get('finished_at'))
        unit.duration_seconds = data.get('duration_seconds')
        unit._started = None
        return unit

    def checkpoint_dict(self) -> dict:
        """``as_dict()`` plus the source counts, for the checkpoint and the workers."""

        return {**self.as_dict(), 'counts': list(self.counts)}

    def describe(self) -> str:
        source = f' ({self.source})' if self.source else ''
        state = ', interrompida' if self.state == INTERRUPTED else ''
        return (
            f'{self.label}{source}: {self.rows_read} linhas lidas, {self.insert_count} inserts, '
            f'{_seconds(self.duration_seconds):.2f} s{state}'
        )

    def summary(self) -> dict:
        return {
            'state': self.state,
            'rows_read': self.rows_read,
            'inserts': dict(self.inserts),
            'duration_seconds': _round(self.duration_seconds),
        }

    def as_dict(self) -> dict:
        return {
            'label': self.label,
            'source': self.source,
            'phase': self.phase,
            'started_at': _timestamp(self.started_at),
            'finished_at': _timestamp(self.finished_at),
            **self.summary(),
            'losses': [group.as_dict() for group in self.losses.values()],
        }


class _NullUnit:
    """Unit of an engine without a collector: counts nothing."""

    def row_read(self):
        pass


NULL_UNIT = _NullUnit()


class Phase:
    """One phase opened by the context, with the units that ran inside it."""

    def __init__(self, name: str, clock, now):
        self.name = name
        self.units = []
        self.native_copies = []
        self.tasks = []
        self.started_at = now()
        self.finished_at = None
        self.duration_seconds = None
        self._started = clock()

    def close(self, clock, now):
        self.finished_at = now()
        self.duration_seconds = clock() - self._started

    def totals(self) -> dict:
        return {
            'units': len(self.units),
            'rows_read': sum(unit.rows_read for unit in self.units),
            'inserts': sum(unit.insert_count for unit in self.units),
        }

    def as_dict(self) -> dict:
        return {
            'phase': self.name,
            'started_at': _timestamp(self.started_at),
            'finished_at': _timestamp(self.finished_at),
            'duration_seconds': _round(self.duration_seconds),
            **self.totals(),
            'native_copies': list(self.native_copies),
            'tasks': len(self.tasks),
            # Next to the phase duration, it shows how much the tasks overlapped.
            'tasks_duration_seconds': (
                _round(sum(task['duration_seconds'] or 0 for task in self.tasks)) if self.tasks else None
            ),
        }


def _no_events(event, message, level='info', data=None):
    pass


class GenerationMetrics:
    """Collector of one generation run.

    ``events(event, message, level, data)`` receives the structured events
    (the logger writes them to ``migration.jsonl``). ``clock`` measures
    durations and ``now`` gives the timestamps of the report.
    """

    def __init__(self, run_id: str = None, events=None, clock=time.monotonic, now=datetime.datetime.now):
        self.run_id = run_id
        self.phases = []
        self.units = []
        self.tasks = []
        # Workers and worker mode of the run, set by the context.
        self.parallel = None
        # Attempts of a resumed run, set from the checkpoint.
        self.attempts = None
        self.status = RUNNING
        self.error = None
        self.started_at = now()
        self.finished_at = None
        self.duration_seconds = None
        self._events = events or _no_events
        self._clock = clock
        self._now = now
        self._started = clock()
        self._open_units = []
        self._phase_open = False
        self._finished = False

    # Current position, for the structured log ---------------------------

    @property
    def phase_name(self):
        return self.phases[-1].name if self._phase_open else None

    @property
    def unit_label(self):
        return self._open_units[-1].label if self._open_units else None

    # Phases ---------------------------------------------------------------

    def open_phase(self, name: str):
        self._close_phase()
        self.phases.append(Phase(name, self._clock, self._now))
        self._phase_open = True
        self._events('phase_started', f'Fase {name} iniciada')

    def _close_phase(self):
        if not self._phase_open:
            return
        phase = self.phases[-1]
        phase.close(self._clock, self._now)
        self._events(
            'phase_finished',
            f'Fase {phase.name} concluída',
            data={'duration_seconds': _round(phase.duration_seconds), **phase.totals()},
        )
        self._phase_open = False

    def native_copy(self, source: str, tool: str, entities, duration_seconds: float, size_bytes: int):
        """Record a native copy (pg_dump) included in the open phase."""

        if self._phase_open:
            self.phases[-1].native_copies.append({
                'source': source,
                'tool': tool,
                'entities': list(entities),
                'duration_seconds': _round(duration_seconds),
                'bytes': size_bytes,
            })

    # Units ----------------------------------------------------------------

    @contextmanager
    def unit(self, label: str, source: str = None):
        """Open a unit for the block. An exception leaving the block closes it as interrupted."""

        unit = Unit(label, source, self.phase_name, self._clock, self._now)
        self.units.append(unit)
        if self._phase_open:
            self.phases[-1].units.append(unit)
        self._open_units.append(unit)
        self._events('unit_started', f'Unidade {label} iniciada', data={'source': source})
        try:
            yield unit
        except BaseException:
            self._close_unit(unit, INTERRUPTED)
            raise
        self._close_unit(unit, DONE)

    def _close_unit(self, unit: Unit, state: str):
        if unit not in self._open_units:
            return
        unit.close(state, self._clock, self._now)
        # The events go out while the unit is still the current one.
        self._events(
            'unit_finished',
            unit.describe(),
            level='warning' if state == INTERRUPTED else 'info',
            data=unit.summary(),
        )
        for group in unit.losses.values():
            self._events('data_loss', group.describe(), level='warning', data=group.as_dict())
        self._open_units.remove(unit)

    def adopt(self, units):
        """Add to the open phase, in merge order, the finished units of a task run in a worker."""

        phase = self.phases[-1] if self._phase_open else None
        for unit in units:
            if isinstance(unit, dict):
                unit = Unit.from_dict(unit)
            unit.phase = phase.name if phase is not None else None
            self.units.append(unit)
            if phase is not None:
                phase.units.append(unit)

    def task_finished(
        self, name: str, sequence: int, worker, started_at, finished_at, duration_seconds: float, resumed: bool = False,
    ):
        """Record a finished task in the open phase. A resumed task came from the checkpoint."""

        record = {
            'name': name,
            'sequence': sequence,
            'phase': self.phase_name,
            'worker': worker,
            'started_at': _timestamp(started_at),
            'finished_at': _timestamp(finished_at),
            'duration_seconds': _round(duration_seconds),
            'resumed': resumed,
        }
        self.tasks.append(record)
        if self._phase_open:
            self.phases[-1].tasks.append(record)

    def counted(self, source: str, query: dict, total):
        """Record in the open unit the source count of a query, given as ``query_to_dict()``."""

        if self._open_units and query is not None and total is not None:
            self._open_units[-1].counts.append({'source': source, 'query': query, 'total': total})

    def inserted(self, entity: str):
        """Count an insert in the open unit. Outside a unit, nothing is counted."""

        if self._open_units:
            self._open_units[-1].inserted(entity)

    def lost(self, entity: str, column: str, strategy: str, lost: LostValue):
        if self._open_units:
            self._open_units[-1].lost(entity, column, strategy, lost)

    # End of the run -------------------------------------------------------

    def finish(self, status: str, error: str = None):
        """Close what is still open and record the result. Later calls only update the result."""

        self.status = status
        self.error = error
        if self._finished:
            return
        for unit in reversed(list(self._open_units)):
            self._close_unit(unit, INTERRUPTED)
        self._close_phase()
        self.finished_at = self._now()
        self.duration_seconds = self._clock() - self._started
        self._finished = True

    def summary(self) -> list:
        """Lines of the end-of-run summary, as (level, text): one per unit, one per loss group."""

        lines = []
        if not self.units:
            return lines
        lines.append(('info', '----- Resumo da geração -----'))
        for unit in self.units:
            lines.append(('warning' if unit.state == INTERRUPTED else 'info', unit.describe()))
        for unit in self.units:
            for group in unit.losses.values():
                lines.append(('warning', '[AVISO] ' + group.describe()))
        return lines

    def report(self, debug_limit: int = None, artifacts=(), run_dir: str = None) -> dict:
        units = [unit.as_dict() for unit in self.units]
        return {
            'run_id': self.run_id,
            'status': self.status,
            'error': self.error,
            'debug': {'enabled': debug_limit is not None, 'limit': debug_limit},
            'started_at': _timestamp(self.started_at),
            'finished_at': _timestamp(self.finished_at),
            'duration_seconds': _round(self.duration_seconds),
            'totals': {
                'units': len(self.units),
                'rows_read': sum(unit.rows_read for unit in self.units),
                'inserts': sum(unit.insert_count for unit in self.units),
                'losses': sum(group.count for unit in self.units for group in unit.losses.values()),
            },
            'parallel': self.parallel,
            'phases': [phase.as_dict() for phase in self.phases],
            'units': units,
            'tasks': list(self.tasks),
            'attempts': self.attempts,
            'artifacts': [_artifact_size(artifact, run_dir) for artifact in artifacts],
        }

    def write_report(self, path: str, debug_limit: int = None, artifacts=(), run_dir: str = None):
        report = self.report(debug_limit, artifacts, run_dir)
        with open(path, 'w', encoding='utf-8') as report_file:
            json.dump(report, report_file, indent=2, ensure_ascii=False)
            report_file.write('\n')


def _artifact_size(artifact: dict, run_dir: str) -> dict:
    path = os.path.join(run_dir, artifact['path']) if run_dir else artifact['path']
    return {**artifact, 'bytes': os.path.getsize(path) if os.path.exists(path) else None}


def _parse_timestamp(value):
    return datetime.datetime.fromisoformat(value) if value else None


def _timestamp(value):
    return value.isoformat(timespec='milliseconds') if value is not None else None


def _round(value):
    return round(value, 3) if value is not None else None


def _seconds(value):
    return value if value is not None else 0.0
