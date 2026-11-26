"""
Aplica no banco de destino os artefatos de uma execução gerada pelo Migracao.py.

Uso:
    python3 Aplicar.py <run_dir> [--dry-run] [--on-error stop|continue]

Exemplos:
    python3 Aplicar.py runs/Acme/20261124-153000 --dry-run
    python3 Aplicar.py runs/Acme/20261124-153000

O adapter de destino vem do manifest.json da execução. A conexão vem das
variáveis TARGET_*, com os mesmos nomes do .env do cliente: TARGET_HOST,
TARGET_PORT, TARGET_NAME, TARGET_USER e TARGET_PASS no PostgreSQL.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from core.apply import APPLY, CONTINUE, DRY_RUN, STOP
from core.apply.cli import run_application
from core.connections import target_connection


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='Aplicar.py',
        description='Aplica os artefatos de comandos/ de uma execução no destino, em transação única.',
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
             f'{CONTINUE} registra o erro e segue (padrão do dry run quando o destino tem savepoint)',
    )
    return parser.parse_args(argv)


def main(argv=None, environ=None) -> int:
    args = parse_args(argv)
    mode = DRY_RUN if args.dry_run else APPLY
    config = target_connection(os.environ if environ is None else environ)
    return run_application(args.run_dir, config, mode, args.on_error)


if __name__ == '__main__':
    sys.exit(main())
