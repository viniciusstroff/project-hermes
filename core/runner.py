import traceback

from core.compatible import copy_compatible_entities
from core.operations import Comment, ResumeConstraints


class MigrationRunner:
    """Runs the standard migration phases against a composed context."""

    def __init__(self, context, migration):
        self.context = context
        self.migration = migration

    def run(self):
        output = self.context.output
        finish_called = False

        try:
            self.context.logger.print_log('----- Início do script de importação V1 - V2 -----')

            self._load_reference_maps()

            self.context.open_phase('prepare_target')
            self._call_hook('run_initial')

            self.context.open_phase('copy_compatible_entities')
            self._copy_compatible_entities()

            self.context.open_phase('load_transformed_data')
            self.context.progress.reset()
            self._call_hook('run_inserts')
            self.context.progress.finish()

            self.context.open_phase('finalize_target')
            self._call_hook('reset_sequences')
            self._resume_constraints()
            output.flush()

            # finish() runs once per run: a failing finish(True) is not
            # followed by finish(False).
            finish_called = True
            output.finish(True)
            self._write_generation_report('success')

            self.context.logger.print_log('----- Fim do script de importação V1 - V2 -----')
            self.context.write_manifest('success')

        except KeyboardInterrupt:
            # Workers were cancelled by the task runner; the run is recorded as failed.
            self.context.progress.finish()
            self.context.logger.print_log('[ERRO] Geração interrompida.', level='error')
            if not finish_called:
                self._finish_failed_output(output)
            self._write_generation_report('failed', 'geração interrompida')
            self.context.write_manifest('failed', 'geração interrompida')
            raise
        except Exception as exc:
            self.context.progress.finish()
            self.context.logger.print_log('[ERRO] ' + str(exc), level='error')
            self.context.logger.print_log(traceback.format_exc(), level='error')
            if not finish_called:
                self._finish_failed_output(output)
            self._write_generation_report('failed', str(exc))
            self.context.write_manifest('failed', str(exc))

    def _finish_failed_output(self, output):
        try:
            output.finish(False)
        except Exception as exc:
            self.context.logger.print_log('[ERRO] Falha ao finalizar o output: ' + str(exc), level='error')

    def _write_generation_report(self, status: str, error: str = None):
        # The report describes the run: failing to write it does not change the run result.
        try:
            self.context.write_generation_report(status, error)
        except Exception as exc:
            self.context.logger.print_log('[ERRO] Falha ao gravar o relatório da geração: ' + str(exc), level='error')

    def _load_reference_maps(self):
        """Load the declared maps, then close target reads before the first phase."""

        hook = getattr(self.migration, 'declare_reference_maps', None)
        declarations = list(hook()) if hook is not None else []
        if declarations:
            self.context.load_reference_maps(declarations)
        self.context.close_target_reads()

    def _copy_compatible_entities(self):
        hook = getattr(self.migration, 'compatible_entities', None)
        entities = list(hook()) if hook is not None else []
        if not entities:
            return
        self.context.logger.print_log('Copiando entidades compatíveis')
        self.context.progress.reset()
        copy_compatible_entities(self.context, entities)
        self.context.progress.finish()

    def _resume_constraints(self):
        """Resume, in the last phase, every suspension emitted during the run."""

        suspended = list(self.context.suspended_constraints)
        if suspended:
            self.context.emit(Comment('HABILITAR TRIGGERS DE TABELAS'))
        for entities in suspended:
            self.context.emit(ResumeConstraints(entities))

    def _call_hook(self, name: str):
        hook = getattr(self.migration, name, None)
        if hook is not None:
            hook()
