import traceback


class MigrationRunner:
    """Runs the standard migration phases against a composed context."""

    def __init__(self, context, migration):
        self.context = context
        self.migration = migration

    def run(self):
        try:
            self.context.logger.print_log('----- Início do script de importação V1 - V2 -----')

            self._prepare_trigger_commands()
            self._call_hook('set_indexes_commands')

            self.context.writer.open_sql_file('prepare_target')
            self._call_hook('run_initial')

            self.context.writer.open_sql_file('load_transformed_data')
            self.context.progress.reset()
            self._call_hook('run_inserts')
            self.context.progress.finish()

            self.context.writer.open_sql_file('finalize_target')
            self._call_hook('reset_sequences')
            self.context.writer.print_comment('HABILITAR ÍNDICES DE TABELAS')
            self.context.writer.write_sql(getattr(self.migration, 'enable_indexes', ''))
            self.context.writer.print_comment('HABILITAR TRIGGERS DE TABELAS')
            self.context.writer.write_sql(getattr(self.migration, 'enable_triggers', ''))
            self.context.writer.flush()

            self.context.logger.print_log('----- Fim do script de importação V1 - V2 -----')
            self.context.write_manifest('success')

        except Exception as exc:
            self.context.progress.finish()
            self.context.logger.print_log('[ERRO] ' + str(exc))
            self.context.logger.print_log(traceback.format_exc())
            self.context.write_manifest('failed', str(exc))

    def _prepare_trigger_commands(self):
        if hasattr(self.migration, 'set_trigger_commands'):
            self.migration.set_trigger_commands()
            return

        self.context.logger.print_log('Gerando comandos de triggers para tabelas')
        self.migration.disable_triggers = self.context.metadata.disable_triggers_sql()
        self.migration.enable_triggers = self.context.metadata.enable_triggers_sql()
        self.context.logger.print_log('Comandos de trigger gerados')

    def _call_hook(self, name: str):
        hook = getattr(self.migration, name, None)
        if hook is not None:
            hook()
