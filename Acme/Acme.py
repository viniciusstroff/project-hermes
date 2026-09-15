import os
import sys
import traceback

_clientdir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(_clientdir))

from core.base_migration import BaseMigration
from core.table_migration import TableMigration
from core.strategies import Copy, EmailExtract, Fixed, Rename


class Migracao(BaseMigration):
    """Migração fictícia usada como modelo sanitizado.

    Este arquivo não representa nenhum cliente, banco ou tabela real. Use-o como
    ponto de partida para criar migrações específicas em diretórios privados.
    """

    def __init__(self):
        super().__init__(_clientdir)
        self.department_map = {
            1: 10,  # Operações
            2: 20,  # Financeiro
            3: 30,  # Atendimento
        }

    def dump_ignoring_tables(self):
        return [
            'public.acme_legacy_audit_log',
            'public.acme_legacy_tmp_import',
            'public.acme_legacy_tmp_import_f_id_seq',
            'public.acme_demo_notes',
        ]

    def dump_non_existing_tables(self):
        return [
            'public.acme_optional_module',
            'public.acme_optional_module_f_id_seq',
            'extranet.acme_portal_messages',
            'extranet.acme_portal_messages_f_id_seq',
        ]

    def set_indexes_commands(self):
        self.print_log('Gerando comandos de indices para tabelas ficticias')

        indexes = [
            'acme_customers_demo_idx',
            'acme_orders_demo_idx',
        ]

        self.set_index_commands_for(indexes)

        self.print_log('Indices ficticios habilitados/desabilitados:')
        self.print_log(', '.join(indexes))

    def run_initial(self):
        self.print_comment('PREPARACAO DAS TABELAS FICTICIAS')
        self.write_sql('TRUNCATE public.acme_customers_demo CASCADE;\n')
        self.write_sql('TRUNCATE public.acme_orders_demo CASCADE;\n')

    def run_inserts(self):
        self.insert_acme_customers()
        self.insert_acme_orders()

    def insert_acme_customers(self):
        self.print_log('Inserindo clientes ficticios da Acme')
        self.print_comment('INSERTS NA TABELA ACME_CUSTOMERS_DEMO')

        TableMigration(
            source_sql='SELECT {fields} FROM public.acme_v1_customers {limit}',
            target='public.acme_customers_demo',
            fields=[
                Copy('f_id'),
                Rename('f_full_name', 'f_name'),
                EmailExtract('f_email'),
                Fixed('f_status', 1),
            ],
        ).run(self.context.engine('pg_v1'))

    def insert_acme_orders(self):
        self.print_log('Inserindo pedidos ficticios da Acme')
        self.print_comment('INSERTS NA TABELA ACME_ORDERS_DEMO')

        TableMigration(
            source_sql='SELECT {fields} FROM public.acme_v1_orders {limit}',
            target='public.acme_orders_demo',
            fields=[
                Copy('f_id'),
                Rename('f_customer_id', 'f_customer'),
                Copy('f_reference'),
                Fixed('f_create_user', 1),
                Fixed('f_status', 1),
            ],
        ).run(self.context.engine('pg_v1'))

    def reset_sequences(self):
        self.print_log('Gerando setval para sequences ficticias')

        tables = [
            'acme_customers_demo',
            'acme_orders_demo',
        ]

        self.write_reset_sequences(tables)

        self.print_log(', '.join(tables))
