import os
import sys
import traceback

_clientdir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(_clientdir))

from core.base_migration import BaseMigration
from core.queries import SourceQuery
from core.references import ReferenceMap
from core.table_migration import TableMigration
from core.strategies import Copy, EmailExtract, Fixed, Lookup, Rename


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

    def declare_reference_maps(self):
        # Lidos da V2 antes da primeira fase. O normalizador vale também para
        # o Lookup que usa o mapa.
        return [
            ReferenceMap(
                'regions_by_code',
                table='public.acme_regions_demo',
                key='f_code',
                value='f_id',
                filters={'f_active': True},
                key_normalizer=lambda value: str(value).strip().upper(),
            ),
        ]

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

    def run_initial(self):
        self.comment('PREPARACAO DAS TABELAS FICTICIAS')
        self.clear(['public.acme_orders_demo', 'public.acme_customers_demo'])

    def run_inserts(self):
        self.insert_acme_customers()
        self.insert_acme_orders()

    def insert_acme_customers(self):
        self.print_log('Inserindo clientes ficticios da Acme')
        self.comment('INSERTS NA TABELA ACME_CUSTOMERS_DEMO')

        TableMigration(
            source=SourceQuery('public.acme_v1_customers'),
            target='public.acme_customers_demo',
            fields=[
                Copy('f_id'),
                Rename('f_full_name', 'f_name'),
                EmailExtract('f_email'),
                Lookup('f_region_code', self.reference_map('regions_by_code'), v2_name='f_region'),
                Fixed('f_status', 1),
            ],
        ).run(self.context.source('v1'))

    def insert_acme_orders(self):
        self.print_log('Inserindo pedidos ficticios da Acme')
        self.comment('INSERTS NA TABELA ACME_ORDERS_DEMO')

        TableMigration(
            source=SourceQuery('public.acme_v1_orders'),
            target='public.acme_orders_demo',
            fields=[
                Copy('f_id'),
                Rename('f_customer_id', 'f_customer'),
                Copy('f_reference'),
                Fixed('f_create_user', 1),
                Fixed('f_status', 1),
            ],
        ).run(self.context.source('v1'))

    def reset_sequences(self):
        self.print_log('Ajustando sequences ficticias')

        tables = [
            'public.acme_customers_demo',
            'public.acme_orders_demo',
        ]

        self.reset_identity(tables, column='f_id')

        self.print_log(', '.join(tables))
