"""Checkpoint of a generation, to resume it in the same run directory.

After each task is merged into its phase artifact, the file is flushed and
synced, and ``checkpoint.json`` records the task name, the start and end
positions of its commands in the artifact, the sha256 of the artifact up to
those positions, the source counts of its units and a hash of its code.

``Migracao.py --resume <timestamp>`` reopens the run. It checks the
fingerprint of the run and the source counts of the finished tasks, then
replays the hooks in skip mode up to the last confirmed task of each phase:
operations outside tasks are rendered and compared with the bytes in the
artifact, finished tasks do not run, and the artifact is truncated where
writing goes on. Nothing is written before the checks pass.
"""

import datetime
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from core.artifacts import PHASES
from core.outputs import FileOutput
from core.queries import query_from_dict

try:
    import fcntl
except ImportError:  # Windows: no lock, the run directory is still checked by status.
    fcntl = None

CHECKPOINT_FILE = 'checkpoint.json'
LOCK_FILE = 'checkpoint.lock'
TASKS_DIR = 'tarefas'
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()
_CHUNK = 1024 * 1024

FINGERPRINT_LABELS = {
    'core': 'código do core',
    'connections': 'conexões e versões dos servidores',
    'connection_params': 'parâmetros das conexões',
    'debug_limit': 'limite de debug',
    'output': 'output',
    'reference_maps': 'mapas de referência',
    'target_catalog': 'catálogo do destino',
}


class CheckpointError(Exception):
    """A run that cannot be resumed, or a resume that diverged from the recorded run."""


@dataclass(frozen=True)
class ResumeSettings:
    """Run to resume, chosen on the command line, and a finished task to run again."""

    timestamp: str
    rerun: str = None


_RESUME = ContextVar('hermes_resume', default=None)


@contextmanager
def resume_settings(settings: ResumeSettings):
    """Resume the run named by ``settings`` in the contexts built inside the block."""

    token = _RESUME.set(settings)
    try:
        yield settings
    finally:
        _RESUME.reset(token)


def current_resume():
    return _RESUME.get()


@dataclass
class PhasePlan:
    """What a resume keeps of a phase: the bytes before ``cut`` and the tasks in them."""

    cut: int = 0
    sha256: str = EMPTY_SHA256
    marks: list = field(default_factory=list)


def check_resumable(run_dir: str):
    """Refuse a run that does not exist, has no checkpoint or already finished. Writes nothing."""

    if not os.path.isdir(run_dir):
        raise CheckpointError(f'A execução {run_dir} não existe.')
    if not os.path.exists(os.path.join(run_dir, CHECKPOINT_FILE)):
        raise CheckpointError(f'A execução {run_dir} não tem {CHECKPOINT_FILE}: ela não pode ser retomada.')
    manifest_path = os.path.join(run_dir, 'manifest.json')
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding='utf-8') as manifest_file:
            status = json.load(manifest_file).get('status')
        if status == 'success':
            raise CheckpointError(f'A execução {run_dir} já terminou com sucesso: não há o que retomar.')


class Checkpoint:
    """The ``checkpoint.json`` of a run, held under a lock while the run goes on."""

    def __init__(self, run_dir: str, data: dict, lock, resume: ResumeSettings = None):
        self.run_dir = run_dir
        self.data = data
        self.resume = resume
        self._lock = lock
        self._plans = {}

    @property
    def path(self) -> str:
        return os.path.join(self.run_dir, CHECKPOINT_FILE)

    @property
    def resuming(self) -> bool:
        return self.resume is not None

    @property
    def attempts(self) -> list:
        return list(self.data['attempts'])

    @classmethod
    def open(cls, run_dir: str, resume: ResumeSettings = None) -> 'Checkpoint':
        lock = _lock(run_dir)
        try:
            if resume is not None:
                with open(os.path.join(run_dir, CHECKPOINT_FILE), encoding='utf-8') as checkpoint_file:
                    data = json.load(checkpoint_file)
            else:
                data = {'version': 1, 'status': 'running', 'error': None, 'fingerprint': None,
                        'attempts': [], 'phases': {}, 'segments': []}
            checkpoint = cls(run_dir, data, lock, resume)
            if resume is not None:
                checkpoint._plans = checkpoint._plan(resume.rerun)
        except BaseException:
            _unlock(lock)
            raise
        checkpoint.data['status'] = 'running'
        checkpoint.data['error'] = None
        checkpoint.data['attempts'].append({'started_at': _now(), 'finished_at': None, 'status': 'running'})
        checkpoint.save()
        return checkpoint

    # Resume plan -----------------------------------------------------------

    def _plan(self, rerun: str = None) -> dict:
        """Per phase, the cut of a resume. After the first phase that is not complete, phases start over."""

        plans = {}
        phases = self.data['phases']
        start_over = False
        rerun_found = rerun is None
        for phase in PHASES:
            info = phases.get(phase)
            if start_over or info is None:
                plans[phase] = PhasePlan()
                start_over = True
                continue
            marks = info['marks']
            names = [mark['name'] for mark in marks]
            if rerun is not None and rerun in names:
                index = names.index(rerun)
                plans[phase] = PhasePlan(marks[index]['start'], marks[index]['start_sha256'], marks[:index])
                rerun_found = True
                start_over = True
                self._drop_segments(phase)
                continue
            closed = info.get('closed')
            if closed:
                plans[phase] = PhasePlan(closed['size'], closed['sha256'], list(marks))
            else:
                last = marks[-1] if marks else None
                plans[phase] = PhasePlan(last['end'] if last else 0, last['sha256'] if last else EMPTY_SHA256, list(marks))
                start_over = True
        if not rerun_found:
            raise CheckpointError(f"A tarefa '{rerun}' não está entre as tarefas concluídas desta execução.")
        return plans

    def plan(self, phase: str):
        return self._plans.get(phase)

    def skipped_marks(self) -> list:
        return [mark for plan in self._plans.values() for mark in plan.marks]

    # Recording --------------------------------------------------------------

    def phase_opened(self, phase: str, kept_marks):
        self.data['phases'][phase] = {'marks': list(kept_marks), 'closed': None}
        self.save()

    def phase_closed(self, phase: str, size: int, sha256: str):
        info = self.data['phases'].setdefault(phase, {'marks': [], 'closed': None})
        info['closed'] = {'size': size, 'sha256': sha256}
        self.save()

    def record_mark(self, phase: str, mark: dict):
        self.data['phases'].setdefault(phase, {'marks': [], 'closed': None})['marks'].append(mark)
        self.data['segments'] = [
            segment for segment in self.data['segments']
            if not (segment['phase'] == phase and segment['name'] == mark['name'])
        ]
        self.save()

    def record_segment(self, phase: str, name: str, path: str, sha256: str, size: int, payload: dict):
        """A task that finished in a worker and was not merged yet."""

        relative = os.path.relpath(path, self.run_dir)
        self.data['segments'] = [
            segment for segment in self.data['segments'] if not (segment['phase'] == phase and segment['name'] == name)
        ]
        self.data['segments'].append({
            'phase': phase, 'name': name, 'path': relative, 'sha256': sha256, 'size': size, 'payload': _jsonable(payload),
        })
        self.save()

    def reusable_segment(self, phase: str, name: str):
        """Payload of a finished segment whose file still matches its sha256, or None."""

        if not self.resuming:
            return None
        for segment in self.data['segments']:
            if segment['phase'] == phase and segment['name'] == name:
                path = os.path.join(self.run_dir, segment['path'])
                if os.path.exists(path) and _file_sha256(path) == segment['sha256']:
                    return {**segment['payload'], 'segment': path}
        return None

    def _drop_segments(self, from_phase: str):
        order = PHASES.index(from_phase)
        self.data['segments'] = [
            segment for segment in self.data['segments'] if PHASES.index(segment['phase']) < order
        ]

    # Start and end of the run ------------------------------------------------

    def begin(self, context):
        """Record the fingerprint of a new run, or check it and the source counts on resume."""

        fingerprint = compute_fingerprint(context)
        if not self.resuming:
            self.data['fingerprint'] = fingerprint
            self.save()
            return
        stored = self.data.get('fingerprint')
        if stored is None:
            # The first attempt failed before reaching this point: nothing was generated.
            self.data['fingerprint'] = fingerprint
            self.save()
            return
        changed = [FINGERPRINT_LABELS.get(key, key) for key in FINGERPRINT_LABELS if stored.get(key) != fingerprint.get(key)]
        if changed:
            raise CheckpointError(
                'A execução não pode ser retomada: mudou desde a primeira tentativa: ' + ', '.join(changed) + '.'
            )
        self._check_source(context)

    def _check_source(self, context):
        """Count again the units of the finished tasks: a changed source would leave orphan rows."""

        payloads = [mark for mark in self.skipped_marks()]
        payloads += [segment['payload'] | {'name': segment['name']} for segment in self.data['segments']]
        changed = []
        engines = {}
        for payload in payloads:
            for unit in payload.get('units', ()):
                for count in unit.get('counts', ()):
                    source = count['source']
                    if source not in engines:
                        engines[source] = context.source(source)
                    now = engines[source].total_rows(query_from_dict(count['query']))
                    if now is not None and now != count['total']:
                        changed.append(f"{payload['name']}/{unit['label']}: {count['total']} -> {now}")
        if changed:
            raise CheckpointError(
                'A origem mudou desde a primeira tentativa; a retomada geraria linhas órfãs. '
                'Contagens que mudaram: ' + '; '.join(changed) + '. Gere a execução do zero.'
            )

    def finish(self, status: str, error: str = None):
        self.data['status'] = status
        self.data['error'] = error
        attempt = self.data['attempts'][-1]
        attempt['finished_at'] = _now()
        attempt['status'] = status
        if status == 'success':
            self.data['segments'] = []
            shutil.rmtree(os.path.join(self.run_dir, TASKS_DIR), ignore_errors=True)
        self.save()

    def save(self):
        temporary = self.path + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as checkpoint_file:
            json.dump(self.data, checkpoint_file, indent=2, ensure_ascii=False)
            checkpoint_file.write('\n')
            checkpoint_file.flush()
            os.fsync(checkpoint_file.fileno())
        os.replace(temporary, self.path)

    def release(self):
        _unlock(self._lock)
        self._lock = None


class CheckpointedFileOutput(FileOutput):
    """FileOutput that records, per phase, where each task ends and the sha256 up to there.

    On resume, a phase with a cut starts in skip mode: writes are rendered and
    compared with the bytes already in the artifact, finished tasks are
    skipped, and writing goes on from the cut, after truncating what was
    written past it.
    """

    def __init__(self, artifacts, target, checkpoint: Checkpoint, buffer_size: int = 500):
        super().__init__(artifacts, target, buffer_size)
        self.checkpoint = checkpoint
        self.phase = None
        self.position = 0
        self._hash = None
        self._path = None
        self._skip_until = None
        self._pending_marks = []
        self._reader = None
        self._task_start = None
        self._task_start_sha256 = None
        self._plan = None

    # Phases -------------------------------------------------------------------

    def open_phase(self, phase: str):
        self._close_phase()
        self._path = self.artifacts.command_path(phase, self.format.extension)
        self.phase = phase
        self.position = 0
        self._hash = hashlib.sha256()
        plan = self.checkpoint.plan(phase)
        if plan is None or plan.cut == 0:
            self.file = open(self._path, 'w+', encoding='utf-8', newline='')
            self._skip_until = None
            self._pending_marks = []
            self.checkpoint.phase_opened(phase, [])
            return
        self._verify_prefix(plan)
        self._reader = open(self._path, 'rb')
        self._skip_until = plan.cut
        self._pending_marks = list(plan.marks)
        # The checkpoint of the phase changes only when writing goes on, so a
        # resume that diverges before the cut leaves it as it was.
        self._plan = plan

    def _verify_prefix(self, plan: PhasePlan):
        if not os.path.exists(self._path) or os.path.getsize(self._path) < plan.cut:
            raise CheckpointError(f'{self._path} é menor que o trecho confirmado ({plan.cut} bytes): não dá para retomar.')
        with open(self._path, 'rb') as artifact:
            remaining = plan.cut
            while remaining:
                chunk = artifact.read(min(_CHUNK, remaining))
                self._hash.update(chunk)
                remaining -= len(chunk)
        if self._hash.hexdigest() != plan.sha256:
            raise CheckpointError(f'{self._path} mudou depois da última tentativa: o sha256 do trecho confirmado não confere.')

    def _close_phase(self):
        if self.phase is None:
            return
        if self._skipping():
            raise CheckpointError(
                f'A fase {self.phase} terminou na posição {self.position}, antes do trecho confirmado '
                f'({self._skip_until} bytes): o script não gera o mesmo que na tentativa anterior.'
            )
        self._sync()
        self.checkpoint.phase_closed(self.phase, self.position, self._hash.hexdigest())
        self._close_files()
        self.phase = None

    def finish(self, success: bool):
        if success:
            self._close_phase()
        elif not self._skipping():
            self.flush()
            if self.file:
                self.file.flush()

    def close(self):
        if not self._skipping():
            super().close()
        self._close_files()

    # Writing ------------------------------------------------------------------

    def write(self, operation):
        rendered = self.target.render(operation)
        if self._skipping():
            self._skip_bytes(''.join(rendered).encode('utf-8'))
            return
        for text in rendered:
            data = text.encode('utf-8')
            self._hash.update(data)
            self.position += len(data)
        self._write_buffer.extend(rendered)
        if len(self._write_buffer) >= self.buffer_size:
            self.flush()

    def include_file(self, path: str):
        if self._skipping():
            # Files come from tasks, and confirmed tasks do not run on resume.
            raise self._divergence('um arquivo foi incluído antes do fim do trecho confirmado')
        self.flush()
        with open(path, encoding='utf-8', newline='') as source:
            while True:
                text = source.read(_CHUNK)
                if not text:
                    break
                data = text.encode('utf-8')
                self._hash.update(data)
                self.position += len(data)
                self.file.write(text)

    def _skip_bytes(self, data: bytes):
        end = self.position + len(data)
        if end > self._skip_until:
            raise self._divergence(f'uma operação passa do trecho confirmado ({self._skip_until} bytes)')
        self._reader.seek(self.position)
        if self._reader.read(len(data)) != data:
            raise self._divergence('o comando gerado agora não é igual ao do artefato')
        self.position = end
        self._resume_writing_at_cut()

    # Tasks --------------------------------------------------------------------

    def confirmed_names(self) -> set:
        return {mark['name'] for mark in self._pending_marks} if self._skipping() else set()

    def resumed_task(self, name: str):
        """The recorded mark of ``name`` when it is the next confirmed task, else None."""

        if not self._skipping():
            return None
        if not self._pending_marks:
            raise self._divergence(f"a tarefa '{name}' começa antes do fim do trecho confirmado")
        mark = self._pending_marks[0]
        if mark['name'] != name or mark['start'] != self.position:
            raise self._divergence(f"esperava a tarefa '{mark['name']}' na posição {mark['start']} e veio '{name}'")
        return mark

    def skip_task(self, mark: dict):
        self._pending_marks.pop(0)
        self.position = mark['end']
        self._resume_writing_at_cut()

    def begin_task(self):
        self.flush()
        self._task_start = self.position
        self._task_start_sha256 = self._hash.copy().hexdigest()

    def end_task(self, task, units, worker, started_at, finished_at, duration_seconds: float):
        self._sync()
        self.checkpoint.record_mark(self.phase, {
            'name': task.name,
            'sequence': task.sequence,
            'start': self._task_start,
            'start_sha256': self._task_start_sha256,
            'end': self.position,
            'sha256': self._hash.hexdigest(),
            'code_hash': getattr(task, 'code_hash', None),
            'worker': worker,
            'started_at': _iso(started_at),
            'finished_at': _iso(finished_at),
            'duration': duration_seconds,
            'units': _jsonable(units),
        })

    # Internals ----------------------------------------------------------------

    def _skipping(self) -> bool:
        return self._skip_until is not None and self.position < self._skip_until

    def _resume_writing_at_cut(self):
        if self._skip_until is None or self.position != self._skip_until:
            return
        # Writing goes on from the cut: what the failed attempt wrote past it goes away.
        self._reader.close()
        self._reader = None
        with open(self._path, 'r+b') as artifact:
            artifact.truncate(self._skip_until)
        self.file = open(self._path, 'a', encoding='utf-8', newline='')
        self._skip_until = None
        self.checkpoint.phase_opened(self.phase, self._plan.marks)
        self._plan = None

    def _sync(self):
        self.flush()
        if self.file:
            self.file.flush()
            os.fsync(self.file.fileno())

    def _close_files(self):
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        if self.file is not None:
            self.file.close()
            self.file = None

    def _divergence(self, detail: str) -> CheckpointError:
        return CheckpointError(
            f'A retomada divergiu na fase {self.phase}, posição {self.position}: {detail}. '
            'Gere a execução do zero ou use --rerun com a tarefa que mudou.'
        )


def compute_fingerprint(context) -> dict:
    """What, if it changed, would make a resumed generation differ from a clean one."""

    return {
        'core': _core_sha256(),
        'connections': context.connections,
        'connection_params': _connection_params_sha256(context),
        'debug_limit': context.debug_limit,
        'output': context.output.describe(),
        'reference_maps': _reference_maps_sha256(context),
        'target_catalog': list(getattr(context.target, '_tables', None) or []),
    }


def _core_sha256() -> str:
    core_dir = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(core_dir):
        dirs[:] = sorted(name for name in dirs if name != '__pycache__')
        for name in sorted(files):
            if name.endswith('.py'):
                path = os.path.join(root, name)
                digest.update(os.path.relpath(path, core_dir).encode('utf-8'))
                with open(path, 'rb') as source:
                    digest.update(source.read())
    return digest.hexdigest()


def _connection_params_sha256(context) -> str:
    """Hash of every connection parameter but the password, the URI included."""

    digest = hashlib.sha256()
    configs = [*getattr(context, 'source_configs', {}).values(), getattr(context, 'target_config', None)]
    for config in configs:
        if config is None:
            continue
        params = getattr(config, 'params', {}) or {}
        items = sorted((key, str(value)) for key, value in params.items() if key not in ('pass', 'password'))
        digest.update(repr((getattr(config, 'prefix', None), getattr(config, 'adapter', None), items)).encode('utf-8'))
    return digest.hexdigest()


def _reference_maps_sha256(context) -> str:
    digest = hashlib.sha256()
    maps = getattr(context, '_loaded_reference_maps', {})
    for name in sorted(maps):
        items = sorted((repr(key), repr(value)) for key, value in maps[name].items())
        digest.update(repr((name, items)).encode('utf-8'))
    return digest.hexdigest()


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(_CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: str) -> str:
    return _file_sha256(path)


def _lock(run_dir: str):
    handle = open(os.path.join(run_dir, LOCK_FILE), 'a+')
    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise CheckpointError(f'A execução {run_dir} está em uso por outro processo.') from None
    return handle


def _unlock(handle):
    if handle is None:
        return
    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
    handle.close()


def _jsonable(value):
    return json.loads(json.dumps(value, default=_json_default))


def _json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    raise TypeError(f'{type(value).__name__} não vai para o checkpoint')


def _iso(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')
