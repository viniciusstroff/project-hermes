"""Native copy of the PostgreSQL source adapter through ``pg_dump``.

Enabled per source with ``SOURCE_<NAME>_NATIVE_COPY=pg_dump``. The binary
comes from ``SOURCE_<NAME>_PG_DUMP`` or from the ``PATH``. pg_dump refuses a
server newer than itself, so the version is checked when the source connects,
before generation, with both versions in the message.
"""

import os
import re
import subprocess

from core.connections import ConnectionConfigError


def version_key(major: int, minor: int = 0) -> int:
    """Comparable major version: 9.6 is 906 and 16 is 1600 (from 10 on, the major is one number)."""

    return major * 100 if major >= 10 else major * 100 + minor


def server_version_key(number: int) -> int:
    """Major version of a libpq server number: 90624 is 906 and 160004 is 1600."""

    return (number // 10000) * 100 if number >= 100000 else number // 100


class PgDumpCopy:
    """Runs pg_dump for a list of tables of one source, data only, as INSERT commands."""

    name = 'pg_dump'
    # Adapter that can apply what pg_dump writes.
    target_adapter = 'postgres'

    def __init__(self, config, binary: str = 'pg_dump', run=None):
        self.config = config
        self.binary = binary
        self._run = run or subprocess.run
        self.version = None

    def check_version(self, server_number: int, server_text: str):
        """Refuse a pg_dump older than the source server, naming both versions."""

        try:
            result = self._run([self.binary, '--version'], capture_output=True, text=True)
        except OSError as exc:
            raise ConnectionConfigError(
                f'{self.config.prefix}_NATIVE_COPY=pg_dump, mas o binário {self.binary} não roda: {exc}'
            ) from exc
        match = re.search(r'\(PostgreSQL\)\s+(\d+)(?:\.(\d+))?(?:\.(\d+))?', result.stdout or '')
        if result.returncode != 0 or match is None:
            raise ConnectionConfigError(
                f'{self.config.prefix}_NATIVE_COPY=pg_dump, mas {self.binary} --version não respondeu '
                f'com a versão: {(result.stdout or result.stderr or "").strip()}'
            )
        major, minor = int(match.group(1)), int(match.group(2) or 0)
        self.version = '.'.join(group for group in match.groups() if group is not None)
        if version_key(major, minor) < server_version_key(server_number):
            raise ConnectionConfigError(
                f'O pg_dump {self.version} ({self.binary}) é mais antigo que o servidor da origem '
                f'{self.config.prefix}, PostgreSQL {server_text}. Aponte {self.config.prefix}_PG_DUMP para um '
                f'pg_dump da mesma versão principal do servidor ou mais novo, ou tire {self.config.prefix}_NATIVE_COPY.'
            )

    def command(self, tables, path: str) -> list:
        config = self.config
        command = [
            self.binary,
            '-h', config.get('host'),
            '-p', config.get('port', '5432'),
            '-U', config.get('user'),
            # -b: large objects. With -t, pg_dump leaves them out unless asked.
            '-a', '-b', '--column-inserts', '--disable-triggers', '-E', 'utf-8', '-w',
            '-f', path,
        ]
        for table in tables:
            command += ['-t', table]
        command.append(config.get('name'))
        return command

    def copy(self, tables, path: str):
        env = dict(os.environ)
        env['PGPASSWORD'] = self.config.get('pass', '')
        result = self._run(self.command(tables, path), env=env, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or '').strip().splitlines()[-1:] or ['sem mensagem']
            raise RuntimeError(f'O pg_dump da origem {self.config.name} falhou: {detail[0]}')
