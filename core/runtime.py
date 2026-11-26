import datetime
import json
import shutil
import sys
import time


LEVELS = ('info', 'warning', 'error')


class MigrationLogger:
    """Writes timestamped migration logs to the log artifact and stdout.

    ``migration.log`` keeps one text line per message. ``migration.jsonl``
    receives one JSON object per event: each message is a ``log`` event and
    the metrics collector adds its own (``phase_started``, ``unit_finished``,
    ``data_loss``...). The current phase and unit come from ``metrics``.
    """

    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.progress = None
        # GenerationMetrics of the run, set by the context factory.
        self.metrics = None
        client, timestamp = getattr(artifacts, 'client_name', None), getattr(artifacts, 'timestamp', None)
        self.run_id = f'{client}/{timestamp}' if client and timestamp else None
        self._log_file = open(self.artifacts.log_path('migration.log'), 'a', encoding='utf-8')
        self._events_file = open(self.artifacts.log_path('migration.jsonl'), 'a', encoding='utf-8')

    def print_log(self, line: str, level: str = 'info'):
        now = datetime.datetime.now()
        text = '[' + now.strftime('%d/%m/%Y %H:%M:%S') + '] ' + line
        if not self._log_file.closed:
            self._log_file.write(text + '\n')
            self._log_file.flush()
        self._write_event(now, level, 'log', line, {})

        if self.progress is not None and self.progress.tui_active:
            self.progress.write_log_line(text)
        else:
            sys.stdout.write(text + '\n')
        sys.stdout.flush()

    def event(self, event: str, message: str, level: str = 'info', data: dict = None, unit: str = None):
        """Write a structured event to ``migration.jsonl`` only.

        ``unit`` names the unit of an event sent by a worker, whose unit the
        collector of the main process does not know.
        """

        self._write_event(datetime.datetime.now(), level, event, message, data or {}, unit)

    def _write_event(self, now, level: str, event: str, message: str, data: dict, unit: str = None):
        if level not in LEVELS:
            raise ValueError(f'nível de log inválido: {level!r}. Use {", ".join(LEVELS)}.')
        if self._events_file.closed:
            return
        record = {
            'ts': now.isoformat(timespec='milliseconds'),
            'run_id': self.run_id,
            'level': level,
            'phase': self.metrics.phase_name if self.metrics is not None else None,
            'unit': unit if unit is not None else (self.metrics.unit_label if self.metrics is not None else None),
            'event': event,
            'message': message,
            'data': data,
        }
        self._events_file.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
        self._events_file.flush()

    def close(self):
        for log_file in (self._log_file, self._events_file):
            if not log_file.closed:
                log_file.close()


class MigrationProgress:
    """Tracks table progress and renders the optional terminal footer."""

    FOOTER_HEIGHT = 3

    def __init__(self, logger: MigrationLogger):
        self.logger = logger
        self._progress_line_active = False
        self._progress_table = None
        self._progress_totals = {}
        self._progress_counts = {}
        self._progress_known_total = 0
        self._progress_known_count = 0
        self._progress_unknown_count = 0
        self._progress_started_at = None
        self._tui_active = False

    @property
    def tui_active(self) -> bool:
        return self._tui_active

    def log_table_progress(self, table: str, count: int, total: int = None, done: bool = False):
        previous_count = self._progress_counts.get(table, 0)
        self._progress_counts[table] = count

        if table not in self._progress_totals:
            self._progress_totals[table] = total
            if total is not None:
                self._progress_known_total += total
        elif self._progress_totals[table] is None and total is not None:
            self._progress_totals[table] = total
            self._progress_known_total += total

        delta = max(count - previous_count, 0)
        if self._progress_totals.get(table) is None:
            self._progress_unknown_count += delta
        else:
            self._progress_known_count += delta

        if self._progress_started_at is None:
            self._progress_started_at = time.monotonic()

        if not sys.stdout.isatty():
            if done:
                processed = self._progress_known_count + self._progress_unknown_count
                if self._progress_known_total > 0:
                    self.logger.print_log(
                        f'Progresso geral: {processed}/{self._progress_known_total} registros processados'
                    )
                else:
                    self.logger.print_log(f'Progresso geral: {processed} registros processados')
            return

        if not self._tui_active:
            self._setup_tui()

        self._progress_table = table
        self.render_footer()

    def _setup_tui(self):
        sys.stdout.write('\n' * self.FOOTER_HEIGHT)
        sys.stdout.flush()
        self._tui_active = True

    def teardown_tui(self):
        if not self._tui_active:
            return
        sys.stdout.write(f'\033[{self.FOOTER_HEIGHT - 1}A\033[J')
        sys.stdout.flush()
        self._tui_active = False

    def footer_lines(self):
        cols = shutil.get_terminal_size().columns
        inner_width = cols - 2

        now = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        label = (self._progress_table or '').split('.')[-1] or '-'
        processed = self._progress_known_count + self._progress_unknown_count
        elapsed = max(time.monotonic() - self._progress_started_at, 0.001) if self._progress_started_at else 0.001
        rate = processed / elapsed if processed > 0 else 0

        if self._progress_known_total > 0:
            ratio = min(max(self._progress_known_count / self._progress_known_total, 0), 1)
            percent = int(ratio * 100)
            unknown_suffix = f' +{self._progress_unknown_count}' if self._progress_unknown_count else ''
            count_str = f'{self._progress_known_count}/{self._progress_known_total}{unknown_suffix}'
            prefix = f' [{now}] {percent:3d}% |'
            suffix = f'| {count_str}  {rate:,.0f} reg/s  {label} '
            bar_width = inner_width - len(prefix) - len(suffix)
            if bar_width >= 4:
                filled = int(bar_width * ratio)
                content = prefix + '█' * filled + '░' * (bar_width - filled) + suffix
            else:
                content = f' [{now}] {percent:3d}%  {count_str}  {rate:,.0f} reg/s  {label} '
        else:
            content = f' [{now}]  {processed} registros  {rate:,.0f} reg/s  {label} '

        content = content[:inner_width].ljust(inner_width)
        return (
            '┌' + '─' * inner_width + '┐',
            '│' + content + '│',
            '└' + '─' * inner_width + '┘',
        )

    def render_footer(self):
        if not sys.stdout.isatty() or not self._tui_active:
            return
        top, middle, bottom = self.footer_lines()
        sys.stdout.write(f'\033[{self.FOOTER_HEIGHT - 1}A\033[J')
        sys.stdout.write(top + '\r\n' + middle + '\r\n' + bottom)
        sys.stdout.flush()
        self._progress_line_active = True

    def write_log_line(self, text: str):
        top, middle, bottom = self.footer_lines()
        sys.stdout.write(f'\033[{self.FOOTER_HEIGHT - 1}A\033[J')
        sys.stdout.write(text + '\r\n')
        sys.stdout.write(top + '\r\n' + middle + '\r\n' + bottom)

    def reset(self):
        self.teardown_tui()
        self._progress_line_active = False
        self._progress_table = None
        self._progress_totals = {}
        self._progress_counts = {}
        self._progress_known_total = 0
        self._progress_known_count = 0
        self._progress_unknown_count = 0
        self._progress_started_at = None

    def finish(self):
        self.teardown_tui()
        self._progress_line_active = False
        self._progress_table = None
