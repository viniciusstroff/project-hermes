"""Generation of the tasks of a migration, in sequence or in parallel.

With one worker, tasks run one after the other on the context of the run, as
before. With more workers, each task runs on a task context: its own source
connections, a segment file as output and a private metrics collector. The
main process merges the segments into the phase in merge order, through the
``include_file()`` of the output, as soon as every earlier task finished. The
phase artifact ends with the same bytes as in sequence.

Workers never write logs, progress or metrics. They send records to the main
process, the only writer. Threads share the process; processes are forked
from the main thread (Linux only) and never touch the connections they
inherit.
"""

import datetime
import multiprocessing
import os
import queue
import re
import signal
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable

from core.adapters.registry import connect_adapter
from core.artifacts import PHASES
from core.connections import ConnectionConfigError
from core.metrics import GenerationMetrics
from core.migration_engine import MigrationEngine
from core.operations import Comment, Insert, Native
from core.outputs import FileOutput

THREAD = 'thread'
PROCESS = 'process'
WORKER_MODES = (THREAD, PROCESS)
TASKS_DIR = 'tarefas'
MAIN_WORKER = 'principal'

# A task fills the load of an entity; preparation and finalization belong to their phases.
TASK_OPERATIONS = (Insert, Comment, Native)

_RECORD_TIMEOUT = 0.2
_PROCESS_JOIN_TIMEOUT = 30


class ParallelSettingsError(ConnectionConfigError):
    """Workers or worker mode the run cannot use, found before any connection."""


class TaskOperationError(TypeError):
    """A task emitted an operation that belongs to the preparation or finalization phase."""


class TaskFailedError(RuntimeError):
    """A task failed, or the target refused a command of its segment."""


class TaskCancelled(Exception):
    """The task stopped because another task failed or the run was interrupted."""


@dataclass(frozen=True)
class ParallelSettings:
    """Workers of the generation, chosen on the command line."""

    workers: int = 1
    mode: str = THREAD

    def __post_init__(self):
        if isinstance(self.workers, bool) or not isinstance(self.workers, int) or self.workers < 1:
            raise ValueError(f'workers precisa ser um inteiro a partir de 1; recebeu {self.workers!r}.')
        if self.mode not in WORKER_MODES:
            raise ValueError(f'modo de worker inválido: {self.mode!r}. Use {THREAD} ou {PROCESS}.')

    def check(self, adapters):
        """Refuse the process mode where fork is not safe, naming the reason."""

        if self.mode != PROCESS or self.workers == 1:
            return
        if not sys.platform.startswith('linux'):
            raise ParallelSettingsError(
                f'O modo processo usa fork e só existe em Linux; aqui é {sys.platform}. Use --worker-mode thread.'
            )
        if 'mongodb' in adapters:
            raise ParallelSettingsError(
                'O modo processo não combina com o adapter mongodb: o pymongo mantém threads próprias, '
                'e um fork com elas pode travar. Use --worker-mode thread.'
            )

    def describe(self) -> dict:
        return {'workers': self.workers, 'mode': self.mode}


@dataclass(frozen=True)
class Task:
    """One unit of work in merge order. ``run(context)`` does it on the given context.

    A task with ``writes_segment`` set writes its segment file itself, such
    as the dump of a native copy.
    """

    name: str
    sequence: int
    run: Callable
    writes_segment: bool = False


# The task context of the current thread. BaseMigration.context reads it, so
# a task method calls self.context.source(), self.comment() and
# self.print_log() as it always did.
_TASK_CONTEXT = ContextVar('hermes_task_context', default=None)


def current_task_context():
    return _TASK_CONTEXT.get()


class _SegmentPath:
    """Gives FileOutput the segment path instead of a phase artifact, without listing it in the manifest."""

    def __init__(self, path: str):
        self.path = path

    def command_path(self, phase: str, extension: str) -> str:
        return self.path


class _TaskLogger:
    def __init__(self, send):
        self._send = send

    def print_log(self, line: str, level: str = 'info'):
        self._send(('log', line, level))


class TaskContext:
    """What a task sees as ``self.context`` inside a worker."""

    def __init__(self, parent, task: Task, segment_path: str, send, cancelled, connect_source):
        self.parent = parent
        self.task = task
        self.config = parent.config
        self.artifacts = parent.artifacts
        self.target = parent.target
        self.debug_limit = parent.debug_limit
        self.segment_path = segment_path
        self.logger = _TaskLogger(send)
        self.metrics = GenerationMetrics(getattr(parent.metrics, 'run_id', None), events=self._event)
        self._send = send
        self._cancelled = cancelled
        self._connect_source = connect_source
        self.output = None
        if not task.writes_segment:
            self.output = FileOutput(_SegmentPath(segment_path), parent.target)
            self.output.open_phase(TASKS_DIR)

    def source(self, source_name: str) -> MigrationEngine:
        return MigrationEngine(
            self._connect_source(source_name),
            self.target,
            self.emit,
            self.debug_limit,
            warn_fn=self.warn_once,
            metrics=self.metrics,
            source_name=source_name,
            cancel_check=self.check_cancelled,
        )

    def emit(self, operation):
        if not isinstance(operation, TASK_OPERATIONS):
            raise TaskOperationError(
                f"A tarefa '{self.task.name}' emitiu {type(operation).__name__}, que pertence à fase de "
                'preparação ou de finalização. Uma tarefa só emite Insert, Comment e Native.'
            )
        self.output.write(operation)

    def warn_once(self, key: str, message: str):
        self._send(('warn', key, message))

    def reference_map(self, name: str):
        return self.parent.reference_map(name)

    def record_native_copy(self, source: str, tool: str, entities, duration_seconds: float, size_bytes: int):
        self._send(('native_copy', source, tool, list(entities), duration_seconds, size_bytes))

    def check_cancelled(self):
        if self._cancelled():
            raise TaskCancelled(f"tarefa '{self.task.name}' cancelada")

    def finish(self) -> list:
        """Close the segment and return the metrics of the units of the task."""

        if self.output is not None:
            self.output.finish(True)
            self.output.close()
        return [unit.as_dict() for unit in self.metrics.units]

    def abort(self):
        if self.output is not None:
            try:
                self.output.close()
            except Exception:
                pass

    def _event(self, event, message, level='info', data=None):
        self._send(('event', event, message, level, data or {}, self.metrics.unit_label))


def run_task_list(context, tasks: list):
    """Run tasks given in merge order, with the workers of the context."""

    if not tasks:
        return
    settings = getattr(context, 'parallel', None) or ParallelSettings()
    if settings.workers == 1:
        for task in tasks:
            _run_inline(context, task)
        return

    progress = getattr(context, 'progress', None)
    if progress is not None:
        # Workers do not draw the footer: task lines take its place.
        progress.finish()
    runner = _ThreadRunner if settings.mode == THREAD else _ProcessRunner
    total = len(tasks)
    done = 0
    for group in _sequence_groups(tasks):
        runner(context, group, min(settings.workers, len(group)), done, total).run()
        done += len(group)
    _remove_empty_dirs(context)


def _run_inline(context, task: Task):
    """One worker: the task runs on the context of the run, writing straight to the output."""

    started = datetime.datetime.now()
    clock = time.monotonic()
    context._running_task = task.name
    try:
        task.run(context)
    finally:
        context._running_task = None
    context.metrics.task_finished(
        task.name, task.sequence, MAIN_WORKER, started, datetime.datetime.now(), time.monotonic() - clock,
    )


def _sequence_groups(tasks):
    group = []
    for task in tasks:
        if group and task.sequence != group[0].sequence:
            yield group
            group = []
        group.append(task)
    if group:
        yield group


def _segment_dir(context) -> str:
    phase = context.metrics.phase_name or 'tarefas'
    number = PHASES.index(phase) if phase in PHASES else 99
    return os.path.join(context.artifacts.run_dir, TASKS_DIR, f'{number:02d}_{phase}')


def _segment_path(context, index: int, task: Task) -> str:
    extension = context.target.artifact_format.extension
    safe = re.sub(r'[^\w.-]+', '_', task.name)
    return os.path.join(_segment_dir(context), f'{task.sequence:03d}_{index:03d}_{safe}.{extension}')


def _remove_empty_dirs(context):
    root = os.path.join(context.artifacts.run_dir, TASKS_DIR)
    if not os.path.isdir(root):
        return
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    if not os.listdir(root):
        os.rmdir(root)


def _execute(context, task: Task, segment_path: str, send, cancelled, connect_source, worker: str) -> dict:
    """Run one task on its task context, in a worker thread or process."""

    task_context = TaskContext(context, task, segment_path, send, cancelled, connect_source)
    token = _TASK_CONTEXT.set(task_context)
    started = datetime.datetime.now()
    clock = time.monotonic()
    try:
        task.run(task_context)
        units = task_context.finish()
    except BaseException:
        task_context.abort()
        raise
    finally:
        _TASK_CONTEXT.reset(token)
    return {
        'units': units,
        'worker': worker,
        'started': started,
        'finished': datetime.datetime.now(),
        'duration': time.monotonic() - clock,
    }


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return f'{type(exc).__name__}: {text}' if text else type(exc).__name__


class _Coordinator:
    """Main side: writes what workers send and merges finished segments in order."""

    def __init__(self, context, tasks, paths, done_before: int, total: int):
        self.context = context
        self.tasks = tasks
        self.paths = paths
        self.results = {}
        self.next_merge = 0
        self.done = done_before
        self.total = total

    @property
    def finished(self) -> bool:
        return self.next_merge == len(self.tasks)

    def handle(self, record):
        kind = record[0]
        if kind == 'log':
            self.context.logger.print_log(record[1], level=record[2])
        elif kind == 'event':
            _, event, message, level, data, unit = record
            self.context.logger.event(event, message, level=level, data=data, unit=unit)
        elif kind == 'warn':
            self.context.warn_once(record[1], record[2])
        elif kind == 'native_copy':
            self.context.metrics.native_copy(*record[1:])
        elif kind == 'done':
            self.results[record[1]] = record[2]
            self._merge_ready()
        elif kind == 'failed':
            _, index, message, details = record
            self.context.logger.print_log(details, level='error')
            raise TaskFailedError(f"A tarefa '{self.tasks[index].name}' falhou: {message}")

    def _merge_ready(self):
        while self.next_merge in self.results:
            index = self.next_merge
            payload = self.results.pop(index)
            task = self.tasks[index]
            path = self.paths[index]
            if os.path.exists(path):
                try:
                    self.context.output.include_file(path)
                except Exception as exc:
                    raise TaskFailedError(f"A tarefa '{task.name}' falhou ao entrar na fase: {_describe(exc)}") from exc
                os.remove(path)
            self.context.metrics.adopt(payload['units'])
            self.context.metrics.task_finished(
                task.name, task.sequence, payload['worker'], payload['started'], payload['finished'], payload['duration'],
            )
            self.done += 1
            self.context.logger.print_log(
                f"Tarefa {task.name} concluída em {payload['duration']:.1f} s ({self.done} de {self.total} tarefas)"
            )
            self.next_merge += 1


class _ThreadRunner:
    """Runs a group of tasks in a thread pool, each thread with its own source connections."""

    def __init__(self, context, tasks, workers, done_before, total):
        self.context = context
        self.tasks = tasks
        self.workers = workers
        self.paths = [_segment_path(context, index, task) for index, task in enumerate(tasks)]
        self.coordinator = _Coordinator(context, tasks, self.paths, done_before, total)
        self._records = queue.Queue()
        self._cancel = threading.Event()
        self._local = threading.local()
        self._opened = []
        self._lock = threading.Lock()

    def run(self):
        os.makedirs(_segment_dir(self.context), exist_ok=True)
        pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix='tarefa')
        try:
            for index in range(len(self.tasks)):
                pool.submit(self._work, index)
            while not self.coordinator.finished:
                try:
                    record = self._records.get(timeout=_RECORD_TIMEOUT)
                except queue.Empty:
                    continue
                self.coordinator.handle(record)
        except BaseException:
            self._cancel.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            self._close_connections()
            # Records sent after a failure still reach the log.
            self._drain()

    def _work(self, index: int):
        if self._cancel.is_set():
            return
        try:
            payload = _execute(
                self.context, self.tasks[index], self.paths[index], self._records.put,
                self._cancel.is_set, self._connect_source, threading.current_thread().name,
            )
        except BaseException as exc:
            self._records.put(('failed', index, _describe(exc), traceback.format_exc()))
        else:
            self._records.put(('done', index, payload))

    def _connect_source(self, name: str):
        adapters = getattr(self._local, 'adapters', None)
        if adapters is None:
            adapters = self._local.adapters = {}
        if name not in adapters:
            adapters[name] = _connect_source(self.context, name)
            with self._lock:
                self._opened.append(adapters[name])
        return adapters[name]

    def _close_connections(self):
        for adapter in self._opened:
            try:
                adapter.close()
            except Exception:
                pass
        self._opened = []

    def _drain(self):
        while True:
            try:
                record = self._records.get_nowait()
            except queue.Empty:
                return
            if record[0] in ('log', 'warn'):
                self.coordinator.handle(record)


class _ProcessRunner:
    """Runs a group of tasks in forked processes, each with its own source connections."""

    def __init__(self, context, tasks, workers, done_before, total):
        self.context = context
        self.tasks = tasks
        self.workers = workers
        self.paths = [_segment_path(context, index, task) for index, task in enumerate(tasks)]
        self.coordinator = _Coordinator(context, tasks, self.paths, done_before, total)

    def run(self):
        os.makedirs(_segment_dir(self.context), exist_ok=True)
        fork = multiprocessing.get_context('fork')
        self._records = fork.Queue()
        self._next = fork.Value('i', 0)
        self._cancel = fork.Value('b', False)
        _flush_before_fork(self.context)
        processes = [fork.Process(target=self._child, args=(number,), name=f'tarefa-{number}') for number in range(self.workers)]
        for process in processes:
            process.start()
        try:
            while not self.coordinator.finished:
                try:
                    record = self._records.get(timeout=_RECORD_TIMEOUT)
                except queue.Empty:
                    if not any(process.is_alive() for process in processes) and self._records.empty():
                        raise TaskFailedError('Um worker terminou sem devolver o resultado da tarefa.')
                    continue
                self.coordinator.handle(record)
        except BaseException:
            self._cancel.value = True
            raise
        finally:
            for process in processes:
                process.join(timeout=_PROCESS_JOIN_TIMEOUT)
                if process.is_alive():
                    process.terminate()
                    process.join()

    def _child(self, number: int):
        # The terminal sends Ctrl+C to the whole group: the main process handles it.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        worker = f'tarefa-{number}'
        adapters = {}

        def connect(name):
            if name not in adapters:
                adapters[name] = _connect_source(self.context, name)
            return adapters[name]

        try:
            while not self._cancel.value:
                with self._next.get_lock():
                    index = self._next.value
                    self._next.value += 1
                if index >= len(self.tasks):
                    break
                try:
                    payload = _execute(
                        self.context, self.tasks[index], self.paths[index], self._records.put,
                        lambda: bool(self._cancel.value), connect, worker,
                    )
                except BaseException as exc:
                    self._records.put(('failed', index, _describe(exc), traceback.format_exc()))
                    break
                self._records.put(('done', index, payload))
        finally:
            # Only the connections this process opened. The inherited ones
            # belong to the main process: closing them here would end its session.
            for adapter in adapters.values():
                try:
                    adapter.close()
                except Exception:
                    pass
            self._records.close()
            self._records.join_thread()


def _connect_source(context, name: str):
    configs = getattr(context, 'source_configs', None) or {}
    if name not in configs:
        from core.context import SourceNotConfiguredError

        raise SourceNotConfiguredError(name, context.sources)
    return connect_adapter(configs[name], 'source')


def _flush_before_fork(context):
    """Write pending buffers, so a child never writes the same bytes again."""

    sys.stdout.flush()
    sys.stderr.flush()
    output = context.output
    output.flush()
    for inner in (output, *getattr(output, 'outputs', ())):
        file = getattr(inner, 'file', None)
        if file is not None:
            file.flush()

