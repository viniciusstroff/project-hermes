"""Console flow shared by ``Aplicar.py`` and ``Migracao.py --apply``.

Both entry points take the target adapter from the run manifest and the
connection from ``TARGET_*`` keys: the environment in ``Aplicar.py`` and the
client ``.env`` in ``Migracao.py``. Then they show the target and the mode,
run the applier and turn the report into an exit code.
"""

import dataclasses
import traceback
from typing import Callable

from core.adapters.registry import AdapterResolutionError, resolve_adapter
from core.application import CapabilityError
from core.apply.applier import ArtifactApplier
from core.apply.execution import FAILED, RESULT_MESSAGES
from core.apply.run import GeneratedRun, IncompleteRunError
from core.connections import ConnectionConfig, ConnectionConfigError

# Exit codes: 1 for a failed or refused application, 2 for a target
# configuration to fix, 130 for an interruption.
EXIT_FAILED = 1
EXIT_CONFIGURATION = 2
EXIT_INTERRUPTED = 130


class TargetConnectionError(Exception):
    """The target database refused the connection."""


def target_label(config: ConnectionConfig) -> str:
    """Where the application goes, without password: ``user@host:port/name`` when there is one."""

    host, name = config.get('host'), config.get('name')
    if host and name:
        port = f":{config.get('port')}" if config.get('port') else ''
        user = f"{config.get('user')}@" if config.get('user') else ''
        return f'{user}{host}{port}/{name}'
    return ', '.join(f'{key}={value}' for key, value in config.describe().items() if key != 'adapter')


def session_opener(adapter_class, config: ConnectionConfig, waits_between_commands: bool = False) -> Callable:
    """Function that connects the target adapter and opens its application session.

    ``waits_between_commands`` asks for a session that keeps its transaction
    while the caller takes time between commands, as the direct output does.
    """

    def open_session():
        try:
            adapter = adapter_class.connect(config)
        except ConnectionConfigError:
            raise
        except Exception as exc:
            raise TargetConnectionError(f'Não foi possível conectar ao destino: {str(exc).strip()}') from exc
        return adapter.session(waits_between_commands=waits_between_commands)

    return open_session


def run_application(
    run_dir: str,
    config: ConnectionConfig,
    mode: str,
    on_error: str = None,
    log: Callable[[str], None] = None,
    open_session: Callable = None,
) -> int:
    """Apply ``run_dir`` to the target of ``config`` and return the process exit code.

    ``log`` defaults to ``print``. ``open_session`` replaces the real connection in tests.
    """

    log = log or print

    try:
        run = GeneratedRun.load(run_dir)
        adapter_class = resolve_adapter(run.target_adapter, 'target')
        config = _config_for_run(config, run.target_adapter, adapter_class)
        applier = ArtifactApplier(
            run,
            open_session or session_opener(adapter_class, config),
            mode=mode,
            on_error=on_error,
            log=log,
        )
    except ConnectionConfigError as exc:
        log(f'[ERRO] {exc}')
        return EXIT_CONFIGURATION
    except (IncompleteRunError, AdapterResolutionError, CapabilityError) as exc:
        log(f'[ERRO] {exc}')
        return EXIT_FAILED

    log(f'Destino ({run.target_adapter}): {target_label(config)}')
    log(f'Modo: {mode}, política de erro: {applier.on_error}')

    try:
        report = applier.apply()
    except (TargetConnectionError, ConnectionConfigError) as exc:
        log(f'[ERRO] {exc}')
        return EXIT_FAILED
    except KeyboardInterrupt:
        log('[ERRO] Aplicação interrompida. A transação foi desfeita.')
        return EXIT_INTERRUPTED
    except Exception as exc:
        log(f'[ERRO] {exc}')
        log(traceback.format_exc())
        return EXIT_FAILED

    return log_report_summary(report, log)


def log_report_summary(report, log: Callable[[str], None]) -> int:
    """Show the result of an application report and return the exit code."""

    log(f'Resultado: {report.result}. {RESULT_MESSAGES[report.result]}.')
    if report.failure:
        log(f'Motivo: {report.failure}')
    log(f'Comandos com erro: {report.error_count}')
    log(f'Relatório: {report.directory}')
    return EXIT_FAILED if report.result == FAILED else 0


def _config_for_run(config: ConnectionConfig, adapter: str, adapter_class) -> ConnectionConfig:
    """The target config with the adapter of the manifest and its required keys present."""

    if config.adapter and config.adapter != adapter:
        raise ConnectionConfigError(
            f"O manifesto registra o destino '{adapter}', mas a conexão de destino usa '{config.adapter}'."
        )
    config = dataclasses.replace(config, adapter=adapter)
    config.require(*getattr(adapter_class, 'required_parameters', ()))
    return config
