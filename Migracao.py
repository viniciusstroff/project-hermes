"""
Ponto de entrada unificado para migrações V1→V2.

Uso:
    python3 Migracao.py <cliente> [--debug [quantidade]] [--apply | --dry-run] [--on-error stop|continue]
                        [--output file|direct|hybrid] [--workers N] [--worker-mode thread|process]
                        [--resume TIMESTAMP [--rerun TAREFA]]

Exemplos:
    python3 Migracao.py Acme
    python3 Migracao.py Acme --debug 100
    python3 Migracao.py Acme --dry-run
    python3 Migracao.py Acme --apply
    python3 Migracao.py Acme --output hybrid --dry-run
    python3 Migracao.py Acme --workers 4
    python3 Migracao.py Acme --resume 20261126-101500

Com --apply ou --dry-run, a execução gerada é aplicada no destino das chaves
TARGET_* do .env do cliente, a mesma conexão que a geração consulta. Com
--output direct ou hybrid, os comandos rodam no destino durante a geração,
numa transação só, e nada é aplicado depois. Com --workers maior que 1, as
tarefas declaradas com @task de uma mesma sequência geram ao mesmo tempo. Com
--resume, uma geração que falhou continua na mesma pasta, sem gerar de novo as
tarefas concluídas.
"""

import argparse
import importlib.util
from contextlib import nullcontext
import os
import sys
import traceback

_rootdir = os.path.dirname(os.path.realpath(__file__))
# Adiciona a raiz do projeto ao path para que core/ seja importável
sys.path.insert(0, _rootdir)

from core.apply import APPLY, CONTINUE, DRY_RUN, STOP
from core.apply.cli import log_report_summary, run_application
from core.adapters.registry import AdapterResolutionError
from core.checkpoint import CheckpointError, ResumeSettings, resume_settings
from core.connections import ConnectionConfigError, parse_connections
from core.context import (
    DIRECT_OUTPUT,
    FILE_OUTPUT,
    HYBRID_OUTPUT,
    OutputSettings,
    output_settings,
    parallel_settings,
)
from core.parallel import THREAD, WORKER_MODES, ParallelSettings
from core.outputs.direct import find_direct_output

DEFAULT_DEBUG_LIMIT = 100


class ClientNotFoundError(Exception):
    """The client directory has no migration script."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='Migracao.py',
        description='Gera os comandos da migração V1→V2 de um cliente e, se pedido, executa no destino.',
    )
    parser.add_argument('client', help='diretório do cliente, por exemplo Acme')
    parser.add_argument(
        '--debug',
        nargs='?',
        const=DEFAULT_DEBUG_LIMIT,
        type=int,
        metavar='QUANTIDADE',
        help=f'no máximo QUANTIDADE linhas por consulta, inclusive na cópia das entidades compatíveis (padrão {DEFAULT_DEBUG_LIMIT})',
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
    parser.add_argument(
        '--output',
        choices=(FILE_OUTPUT, DIRECT_OUTPUT, HYBRID_OUTPUT),
        default=FILE_OUTPUT,
        help='file grava os arquivos (padrão); direct executa no destino durante a geração; '
             'hybrid faz os dois. direct e hybrid exigem --apply ou --dry-run',
    )

    parser.add_argument(
        '--workers',
        type=int,
        default=1,
        metavar='N',
        help='tarefas da mesma sequência geradas ao mesmo tempo (padrão 1, em sequência)',
    )
    parser.add_argument(
        '--worker-mode',
        choices=WORKER_MODES,
        default=THREAD,
        help='thread (padrão) ou process; process usa fork, só em Linux e sem MongoDB',
    )

    parser.add_argument(
        '--resume',
        metavar='TIMESTAMP',
        help='retoma a geração que falhou em runs/<cliente>/<TIMESTAMP>, sem gerar de novo as tarefas concluídas',
    )
    parser.add_argument(
        '--rerun',
        metavar='TAREFA',
        help='na retomada, gera de novo a tarefa concluída e tudo o que vem depois dela na fase',
    )

    args = parser.parse_args(argv)
    if args.rerun and not args.resume:
        parser.error('--rerun exige --resume')
    if args.resume and args.output != FILE_OUTPUT:
        parser.error('--resume só funciona com --output file: a transação no destino já foi desfeita na falha')
    if args.workers < 1:
        parser.error('--workers precisa ser 1 ou mais')
    if args.on_error and not args.application:
        parser.error('--on-error exige --apply ou --dry-run')
    if args.output != FILE_OUTPUT and not args.application:
        parser.error(f'--output {args.output} executa no destino e exige --apply ou --dry-run')
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
    migration.run()


def apply_generated_run(migration, mode: str, on_error: str = None, log=print) -> int:
    # The artifacts are only complete on disk after the output is closed.
    migration.context.close()
    try:
        _, target = parse_connections(migration.config)
    except ConnectionConfigError as exc:
        log(f'[ERRO] {exc}')
        return 1
    return run_application(migration.artifacts.run_dir, target, mode, on_error, log=log)


def report_direct_run(migration, log=None) -> int:
    """Show the result of a run whose commands ran on the target during the generation."""

    log = log or print
    output = find_direct_output(migration.context.output)
    migration.context.close()
    report = output.report if output is not None else None
    if report is None or report.result is None:
        log('[ERRO] A execução não chegou ao destino. Veja o manifest.json e o logs/migration.log da execução.')
        return 1
    return log_report_summary(report, log)


def main(argv=None) -> int:
    args = parse_args(argv)
    direct = args.output != FILE_OUTPUT
    settings = OutputSettings(
        args.output,
        args.application if direct else None,
        args.on_error if direct else None,
    )

    try:
        # The client builds its context in the constructor: the choices reach the factory from here.
        resume = resume_settings(ResumeSettings(args.resume, args.rerun)) if args.resume else nullcontext()
        with output_settings(settings), parallel_settings(ParallelSettings(args.workers, args.worker_mode)), resume:
            migration = load_migration(args.client)
        generate(migration, args.debug)
    except (ClientNotFoundError, ConnectionConfigError, AdapterResolutionError, CheckpointError) as exc:
        # Configuration problems: the message says what to fix, a traceback adds nothing.
        print(f'[ERRO] {exc}')
        return 1
    except Exception as exc:
        print('[ERRO] ' + str(exc))
        print(traceback.format_exc())
        return 1

    if direct:
        return report_direct_run(migration)
    if args.application is None:
        return 0
    return apply_generated_run(migration, args.application, args.on_error)


if __name__ == '__main__':
    sys.exit(main())
