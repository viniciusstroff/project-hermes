"""
Aplica no banco de destino os arquivos SQL de uma execução gerada pelo Migracao.py.

Uso:
    python3 Aplicar.py <run_dir> [--dry-run] [--on-error stop|continue]

Exemplos:
    python3 Aplicar.py runs/Acme/20261124-153000 --dry-run
    python3 Aplicar.py runs/Acme/20261124-153000

A conexão vem das variáveis da libpq, como no psql: PGHOST, PGPORT,
PGDATABASE, PGUSER e PGPASSWORD. PGHOST e PGDATABASE são obrigatórias.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from core.apply import APPLY, CONTINUE, DRY_RUN, STOP
from core.apply.cli import MissingTargetError, libpq_connect, libpq_target, run_application


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='Aplicar.py',
        description='Aplica os arquivos .sql de comandos/ de uma execução no destino, em transação única.',
    )
    parser.add_argument('run_dir', help='pasta da execução, por exemplo runs/Acme/20261124-153000')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='executa tudo e termina em ROLLBACK, sem alterar o destino',
    )
    parser.add_argument(
        '--on-error',
        choices=(STOP, CONTINUE),
        help=f'{STOP} desfaz tudo no primeiro erro (padrão do apply); '
             f'{CONTINUE} registra o erro e segue (padrão do dry run)',
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        target = libpq_target()
    except MissingTargetError as exc:
        print(f'[ERRO] {exc}')
        return 2

    mode = DRY_RUN if args.dry_run else APPLY
    return run_application(args.run_dir, libpq_connect, target, mode, args.on_error)


if __name__ == '__main__':
    sys.exit(main())
