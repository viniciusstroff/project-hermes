"""Report of one application, written inside the run directory.

Each application gets ``aplicacoes/<YYYYmmdd-HHMMSS>-<mode>/`` with:

- ``relatorio.json``: mode, error policy, target without password,
  timestamps, result and counters per file;
- ``erros.txt``: one block per failing statement, written as the errors
  happen, so it can be read during a long application.
"""

import datetime
import json
import os
import time
from dataclasses import dataclass, field

REPORTS_DIR = 'aplicacoes'
REPORT_FILE = 'relatorio.json'
ERRORS_FILE = 'erros.txt'
STATEMENT_PREVIEW = 500


@dataclass
class _FileCounters:
    file: str
    started: float
    executed: int = 0
    errors: int = 0
    skipped: dict = field(default_factory=dict)
    duration_seconds: float = None

    def as_dict(self) -> dict:
        return {
            'file': self.file,
            'executed': self.executed,
            'errors': self.errors,
            'skipped': dict(self.skipped),
            'duration_seconds': self.duration_seconds,
        }


class ApplicationReport:
    """Collects the counters of one application and writes its files.

    ``target`` must not contain the password. ``clock`` exists for tests.
    """

    def __init__(self, run_dir: str, mode: str, on_error: str, target: dict, clock=datetime.datetime.now):
        self.mode = mode
        self.on_error = on_error
        self.target = dict(target)
        self.result = None
        self.failure = None
        self.error_count = 0

        self._clock = clock
        self.started_at = clock()
        self.finished_at = None
        self.directory = _create_directory(
            os.path.join(run_dir, REPORTS_DIR),
            f'{self.started_at:%Y%m%d-%H%M%S}-{mode}',
        )
        self._files = {}
        self._errors_file = open(os.path.join(self.directory, ERRORS_FILE), 'w', encoding='utf-8')

    @property
    def report_path(self) -> str:
        return os.path.join(self.directory, REPORT_FILE)

    @property
    def errors_path(self) -> str:
        return os.path.join(self.directory, ERRORS_FILE)

    def start_file(self, name: str):
        self._files[name] = _FileCounters(name, time.perf_counter())

    def end_file(self, name: str):
        counters = self._files[name]
        counters.duration_seconds = round(time.perf_counter() - counters.started, 3)

    def file_counters(self, name: str) -> dict:
        return self._files[name].as_dict()

    def record_executed(self, name: str, count: int = 1):
        self._files[name].executed += count

    def record_skipped(self, name: str, reason: str):
        skipped = self._files[name].skipped
        skipped[reason] = skipped.get(reason, 0) + 1

    def record_error(self, name: str, number: int, statement, exc: BaseException):
        self._files[name].errors += 1
        self.error_count += 1

        pgcode = getattr(exc, 'pgcode', None)
        sqlstate = f'SQLSTATE {pgcode}' if pgcode else 'sem SQLSTATE'
        self._errors_file.write(
            f'== {name}, comando {number}, linha {statement.line}, {sqlstate}\n'
            f'{str(exc).strip()}\n'
            f'-- comando:\n'
            f'{_preview(statement.text)}\n\n'
        )
        self._errors_file.flush()

    def finish(self, result: str, failure: str = None):
        self.result = result
        self.failure = failure
        self.finished_at = self._clock()

        now = time.perf_counter()
        for counters in self._files.values():
            if counters.duration_seconds is None:
                counters.duration_seconds = round(now - counters.started, 3)

        self._errors_file.close()
        with open(self.report_path, 'w', encoding='utf-8') as report_file:
            json.dump(self.as_dict(), report_file, indent=2, ensure_ascii=False)
            report_file.write('\n')

    def as_dict(self) -> dict:
        return {
            'mode': self.mode,
            'on_error': self.on_error,
            'target': self.target,
            'started_at': self.started_at.isoformat(timespec='seconds'),
            'finished_at': self.finished_at.isoformat(timespec='seconds') if self.finished_at else None,
            'result': self.result,
            'failure': self.failure,
            'errors': self.error_count,
            'files': [counters.as_dict() for counters in self._files.values()],
        }


def _create_directory(parent: str, name: str) -> str:
    os.makedirs(parent, exist_ok=True)
    candidate = name
    suffix = 1
    while True:
        path = os.path.join(parent, candidate)
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            suffix += 1
            candidate = f'{name}-{suffix}'


def _preview(text: str) -> str:
    if len(text) <= STATEMENT_PREVIEW:
        return text
    return f'{text[:STATEMENT_PREVIEW]} ... [truncado, {len(text)} caracteres]'
