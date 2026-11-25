import traceback

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

            output.open_phase('prepare_target')
            self._call_hook('run_initial')

            output.open_phase('load_transformed_data')
            self.context.progress.reset()
            self._call_hook('run_inserts')
            self.context.progress.finish()

            output.open_phase('finalize_target')
            self._call_hook('reset_sequences')
            self._resume_constraints()
            output.flush()

            # finish() runs once per run: a failing finish(True) is not
            # followed by finish(False).
            finish_called = True
            output.finish(True)

            self.context.logger.print_log('----- Fim do script de importação V1 - V2 -----')
            self.context.write_manifest('success')

        except Exception as exc:
            self.context.progress.finish()
            self.context.logger.print_log('[ERRO] ' + str(exc))
            self.context.logger.print_log(traceback.format_exc())
            if not finish_called:
                self._finish_failed_output(output)
            self.context.write_manifest('failed', str(exc))

    def _finish_failed_output(self, output):
        try:
            output.finish(False)
        except Exception as exc:
            self.context.logger.print_log('[ERRO] Falha ao finalizar o output: ' + str(exc))

    def _load_reference_maps(self):
        """Load the declared maps, then close target reads before the first phase."""

        hook = getattr(self.migration, 'declare_reference_maps', None)
        declarations = list(hook()) if hook is not None else []
        if declarations:
            self.context.load_reference_maps(declarations)
        self.context.close_target_reads()

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
