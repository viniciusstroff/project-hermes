"""Console flow shared by ``Aplicar.py`` and ``Migracao.py --apply``.

Both entry points show the target and the mode, run the applier and turn
the report into an exit code. They differ only in where the connection
comes from: libpq environment variables in ``Aplicar.py`` and the
``PG_V2_*`` keys of the client ``.env`` in ``Migracao.py``.
"""

import getpass
import os
import traceback
from typing import Callable

from core.apply.applier import COMMITTED, FAILED, ROLLED_BACK, SqlArtifactApplier
from core.apply.run import IncompleteRunError

# libpq reads these when psycopg2.connect() gets no host or database.
REQUIRED_LIBPQ_VARIABLES = ('PGHOST', 'PGDATABASE')

_RESULT_MESSAGES = {
    COMMITTED: 'COMMIT executado, carga gravada no destino',
    ROLLED_BACK: 'ROLLBACK executado, destino sem alterações',
    FAILED: 'ROLLBACK executado, nada da carga foi gravado',
}


class MissingTargetError(Exception):
    """The environment does not name the target database."""


class TargetConnectionError(Exception):
    """The target database refused the connection."""


def libpq_target(environ=None) -> dict:
    environ = os.environ if environ is None else environ
    missing = [name for name in REQUIRED_LIBPQ_VARIABLES if not environ.get(name)]
    if missing:
        raise MissingTargetError(
            f'Defina {" e ".join(missing)} com o banco de destino. '
            'PGPORT, PGUSER e PGPASSWORD são opcionais, como no psql.'
        )
    return {
        'host': environ['PGHOST'],
        'port': environ.get('PGPORT') or '5432',
        'database': environ['PGDATABASE'],
        'user': environ.get('PGUSER') or getpass.getuser(),
    }


def libpq_connect():
    return _connect()


def config_target(config: dict) -> dict:
    return {
        'host': config['PG_V2_HOST'],
        'port': config['PG_V2_PORT'],
        'database': config['PG_V2_NAME'],
        'user': config['PG_V2_USER'],
    }


def config_connect(config: dict) -> Callable:
    def connect():
        return _connect(
            host=config['PG_V2_HOST'],
            port=config['PG_V2_PORT'],
            dbname=config['PG_V2_NAME'],
            user=config['PG_V2_USER'],
            password=config['PG_V2_PASS'],
        )

    return connect


def run_application(
    run_dir: str,
    connect: Callable,
    target: dict,
    mode: str,
    on_error: str = None,
    log: Callable[[str], None] = print,
) -> int:
    """Apply ``run_dir`` and return the process exit code."""

    applier = SqlArtifactApplier(run_dir, connect, mode=mode, on_error=on_error, log=log)
    log(f'Destino: {target["user"]}@{target["host"]}:{target["port"]}/{target["database"]}')
    log(f'Modo: {mode}, política de erro: {applier.on_error}')

    try:
        report = applier.apply()
    except (IncompleteRunError, TargetConnectionError) as exc:
        log(f'[ERRO] {exc}')
        return 1
    except KeyboardInterrupt:
        log('[ERRO] Aplicação interrompida. A transação foi desfeita.')
        return 130
    except Exception as exc:
        log(f'[ERRO] {exc}')
        log(traceback.format_exc())
        return 1

    log(f'Resultado: {report.result}. {_RESULT_MESSAGES[report.result]}.')
    if report.failure:
        log(f'Motivo: {report.failure}')
    log(f'Comandos com erro: {report.error_count}')
    log(f'Relatório: {report.directory}')
    return 1 if report.result == FAILED else 0


def _connect(**parameters):
    import psycopg2

    try:
        # The generated files and pg_dump -E utf-8 are UTF-8.
        return psycopg2.connect(client_encoding='UTF8', **parameters)
    except psycopg2.Error as exc:
        raise TargetConnectionError(f'Não foi possível conectar ao destino: {str(exc).strip()}') from exc
