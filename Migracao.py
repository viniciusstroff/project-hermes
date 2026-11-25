"""
Ponto de entrada unificado para migrações V1→V2.

Uso:
    python3 Migracao.py <cliente> [--debug [quantidade]] [--apply | --dry-run] [--on-error stop|continue]

Exemplos:
    python3 Migracao.py Acme
    python3 Migracao.py Acme --debug 100
    python3 Migracao.py Acme --dry-run
    python3 Migracao.py Acme --apply

Com --apply ou --dry-run, a execução gerada é aplicada no destino das chaves
PG_V2_* do .env do cliente, a mesma conexão que a geração consulta.
"""

import argparse
import importlib.util
import os
import sys
import traceback

_rootdir = os.path.dirname(os.path.realpath(__file__))
# Adiciona a raiz do projeto ao path para que core/ seja importável
sys.path.insert(0, _rootdir)

from core.apply import APPLY, CONTINUE, DRY_RUN, STOP
from core.apply.cli import config_connect, config_target, run_application

DEFAULT_DEBUG_LIMIT = 100


class ClientNotFoundError(Exception):
    """The client directory has no migration script."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='Migracao.py',
        description='Gera os scripts SQL da migração V1→V2 de um cliente.',
    )
    parser.add_argument('client', help='diretório do cliente, por exemplo Acme')
    parser.add_argument(
        '--debug',
        nargs='?',
        const=DEFAULT_DEBUG_LIMIT,
        type=int,
        metavar='QUANTIDADE',
        help=f'no máximo QUANTIDADE linhas por consulta e sem pg_dump (padrão {DEFAULT_DEBUG_LIMIT})',
    )
    application = parser.add_mutually_exclusive_group()
    application.add_argument(
        '--apply',
        dest='application',
        action='store_const',
        const=APPLY,
        help='aplica a execução gerada no destino e termina em COMMIT',
    )
    application.add_argument(
        '--dry-run',
        dest='application',
        action='store_const',
        const=DRY_RUN,
        help='aplica a execução gerada no destino e termina em ROLLBACK',
    )
    parser.add_argument(
        '--on-error',
        choices=(STOP, CONTINUE),
        help='política de erro da aplicação; exige --apply ou --dry-run',
    )

    args = parser.parse_args(argv)
    if args.on_error and not args.application:
        parser.error('--on-error exige --apply ou --dry-run')
    return args


def load_migration(client: str):
    client_file = os.path.join(_rootdir, client, f'{client}.py')
    if not os.path.isfile(client_file):
        raise ClientNotFoundError(f'Arquivo de migração não encontrado: {client_file}')

    spec = importlib.util.spec_from_file_location(client, client_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Migracao()


def generate(migration, debug_limit):
    if debug_limit is not None:
        migration.enable_debug(debug_limit)
    else:
        migration.dump_tables()
    migration.run()


def apply_generated_run(migration, mode: str, on_error: str = None, log=print) -> int:
    # The SQL files are only complete on disk after the output is closed.
    migration.context.close()
    config = migration.config
    return run_application(
        migration.artifacts.run_dir,
        config_connect(config),
        config_target(config),
        mode,
        on_error,
        log=log,
    )


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        migration = load_migration(args.client)
        generate(migration, args.debug)
    except ClientNotFoundError as exc:
        print(f'[ERRO] {exc}')
        return 1
    except Exception as exc:
        print('[ERRO] ' + str(exc))
        print(traceback.format_exc())
        return 1

    if args.application is None:
        return 0
    return apply_generated_run(migration, args.application, args.on_error)


if __name__ == '__main__':
    sys.exit(main())
