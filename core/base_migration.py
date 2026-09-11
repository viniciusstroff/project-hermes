"""
Infraestrutura compartilhada entre todos os scripts de migração V1→V2.

Cada Acme herda BaseMigration e implementa:
  - dump_ignoring_tables()    → list[str]  tabelas a excluir do pg_dump
  - dump_non_existing_tables()→ list[str]  tabelas inexistentes no Acme
  - set_indexes_commands()    → configura self.disable_indexes / self.enable_indexes
  - reset_sequences()         → gera setval para as sequences do Acme
  - run_initial()             → conteúdo do cmd_ini.sql (truncates, deletes, etc.)
  - run_inserts()             → conteúdo do cmd.sql (inserts e updates)

O método run() é um template: gerencia abertura de arquivos, triggers e índices.
"""

import datetime
import os
import psycopg2
import psycopg2.extras
import re
import shutil
import subprocess
import shlex
import sys
import time
import traceback
import firebirdsql
from dotenv import dotenv_values

from core.migration_engine import MigrationEngine
from core.table_migration import TableMigration
from core.strategies import Copy, Rename, Fixed, Lookup, RegexClean, DateConvert, EmailExtract, CpfClean, PhoneClean, SqlExpression


class BaseMigration:

    # ------------------------------------------------------------------
    # Inicialização
    # ------------------------------------------------------------------

    def enable_debug(self, quantidade=100):
        self.limit = f'LIMIT {quantidade}'

    def __init__(self, clientdir: str):
        """
        clientdir — caminho absoluto do diretório do script Acme
                    (passa os.path.dirname(os.path.realpath(__file__)))
        """
        self._clientdir = clientdir
        self.config = dotenv_values(os.path.join(clientdir, '.env'))
        self.limit = ''
        self._write_buffer = []
        self._buffer_size = 500
        self._cursor_seq = 0
        self._progress_line_active = False
        self._progress_table = None
        self._progress_totals = {}
        self._progress_counts = {}
        self._progress_known_total = 0
        self._progress_known_count = 0
        self._progress_unknown_count = 0
        self._progress_started_at = None
        self._tui_active = False
        self._tui_rows = 0
        self._tui_cols = 0
        self._reference_maps = {}

        self.print_log('----- Conectando BD V1 -----')
        self.file = open(os.path.join(clientdir, self.config['EMPTY_FILENAME']), 'w+', encoding='utf-8')

        self.pg_v1_conn = psycopg2.connect(
            host=self.config['PG_V1_HOST'], port=self.config['PG_V1_PORT'],
            dbname=self.config['PG_V1_NAME'],
            user=self.config['PG_V1_USER'], password=self.config['PG_V1_PASS'])

        self.print_log('----- Conectando BD V2 -----')
        self.pg_v2_conn = psycopg2.connect(
            host=self.config['PG_V2_HOST'], port=self.config['PG_V2_PORT'],
            dbname=self.config['PG_V2_NAME'],
            user=self.config['PG_V2_USER'], password=self.config['PG_V2_PASS'])

        self.print_log('----- Conectando BD Firebird V1 -----')
        self.fb_v1_conn = firebirdsql.connect(
            host=self.config['FB_V1_HOST'],
            port=self.config['FB_V1_PORT'],
            database=self.config['FB_V1_NAME'],
            user=self.config['FB_V1_USER'],
            password=self.config['FB_V1_PASS'],
            charset=self.config['FB_V1_CHARSET'])

        self.disable_triggers = ''
        self.enable_triggers = ''
        self.disable_indexes = ''
        self.enable_indexes = ''

    def __del__(self):
        try:
            if hasattr(self, '_write_buffer'):
                self._flush_writes()
            if hasattr(self, 'pg_v1_conn'):
                self.pg_v1_conn.close()
            if hasattr(self, 'pg_v2_conn'):
                self.pg_v2_conn.close()
            if hasattr(self, 'fb_v1_conn'):
                self.fb_v1_conn.close()
            if hasattr(self, 'file'):
                self.file.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Engine de estratégias
    # ------------------------------------------------------------------

    @property
    def _engine(self) -> MigrationEngine:
        """Cria um MigrationEngine com o limit atual. Chamado por cada TableMigration."""
        return MigrationEngine(
            self.pg_v1_conn,
            self.write_sql,
            self.limit,
            progress_fn=self._log_table_progress,
        )

    @property
    def _fb_engine(self) -> MigrationEngine:
        """Cria um MigrationEngine usando Firebird como fonte."""
        return MigrationEngine(
            self.fb_v1_conn,
            self.write_sql,
            self.limit,
            progress_fn=self._log_table_progress,
            source='firebird',
        )

    def _log_table_progress(self, table: str, count: int, total: int = None, done: bool = False):
        previous_count = self._progress_counts.get(table, 0)
        self._progress_counts[table] = count

        if table not in self._progress_totals:
            self._progress_totals[table] = total
            if total is not None:
                self._progress_known_total += total
        elif self._progress_totals[table] is None and total is not None:
            self._progress_totals[table] = total
            self._progress_known_total += total

        delta = max(count - previous_count, 0)
        if self._progress_totals.get(table) is None:
            self._progress_unknown_count += delta
        else:
            self._progress_known_count += delta

        if self._progress_started_at is None:
            self._progress_started_at = time.monotonic()

        if not sys.stdout.isatty():
            if done:
                processed = self._progress_known_count + self._progress_unknown_count
                if self._progress_known_total > 0:
                    self.print_log(
                        f'Progresso geral: {processed}/{self._progress_known_total} registros processados'
                    )
                else:
                    self.print_log(f'Progresso geral: {processed} registros processados')
            return

        if not self._tui_active:
            self._setup_tui()

        self._progress_table = table
        self._render_progress_footer()

    _FOOTER_HEIGHT = 3  # top border + content + bottom border

    def _setup_tui(self):
        """Cria espaço para o footer imprimindo linhas em branco abaixo do cursor."""
        sys.stdout.write('\n' * self._FOOTER_HEIGHT)
        sys.stdout.flush()
        self._tui_active = True

    def _teardown_tui(self):
        """Remove o footer subindo o cursor e limpando até o fim da tela."""
        if not self._tui_active:
            return
        sys.stdout.write(f'\033[{self._FOOTER_HEIGHT - 1}A\033[J')
        sys.stdout.flush()
        self._tui_active = False

    def _footer_lines(self):
        """Retorna as 3 linhas do footer (top, middle, bottom) sem \\r\\n."""
        cols = shutil.get_terminal_size().columns
        inner_width = cols - 2

        now = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        label = (self._progress_table or '').split('.')[-1] or '-'
        processed = self._progress_known_count + self._progress_unknown_count
        elapsed = max(time.monotonic() - self._progress_started_at, 0.001) if self._progress_started_at else 0.001
        rate = processed / elapsed if processed > 0 else 0

        if self._progress_known_total > 0:
            ratio = min(max(self._progress_known_count / self._progress_known_total, 0), 1)
            percent = int(ratio * 100)
            unknown_suffix = f' +{self._progress_unknown_count}' if self._progress_unknown_count else ''
            count_str = f'{self._progress_known_count}/{self._progress_known_total}{unknown_suffix}'
            prefix = f' [{now}] {percent:3d}% |'
            suffix = f'| {count_str}  {rate:,.0f} reg/s  {label} '
            bar_width = inner_width - len(prefix) - len(suffix)
            if bar_width >= 4:
                filled = int(bar_width * ratio)
                content = prefix + '█' * filled + '░' * (bar_width - filled) + suffix
            else:
                content = f' [{now}] {percent:3d}%  {count_str}  {rate:,.0f} reg/s  {label} '
        else:
            content = f' [{now}]  {processed} registros  {rate:,.0f} reg/s  {label} '

        content = content[:inner_width].ljust(inner_width)
        return (
            '┌' + '─' * inner_width + '┐',
            '│' + content + '│',
            '└' + '─' * inner_width + '┘',
        )

    def _render_progress_footer(self):
        if not sys.stdout.isatty() or not self._tui_active:
            return
        top, middle, bottom = self._footer_lines()
        # Sobe até o início do footer, apaga tudo abaixo, redesenha
        sys.stdout.write(f'\033[{self._FOOTER_HEIGHT - 1}A\033[J')
        sys.stdout.write(top + '\r\n' + middle + '\r\n' + bottom)
        sys.stdout.flush()
        self._progress_line_active = True

    def _reset_progress(self):
        self._teardown_tui()
        self._progress_line_active = False
        self._progress_table = None
        self._progress_totals = {}
        self._progress_counts = {}
        self._progress_known_total = 0
        self._progress_known_count = 0
        self._progress_unknown_count = 0
        self._progress_started_at = None

    def _finish_progress(self):
        self._teardown_tui()
        self._progress_line_active = False
        self._progress_table = None

    # ------------------------------------------------------------------
    # I/O de SQL
    # ------------------------------------------------------------------

    def write_sql(self, sql: str):
        self._write_buffer.append(sql)
        if len(self._write_buffer) >= self._buffer_size:
            self._flush_writes()

    def _flush_writes(self):
        if self._write_buffer:
            self.file.write(''.join(self._write_buffer))
            self._write_buffer = []

    def _v1_cursor(self, factory=psycopg2.extras.RealDictCursor):
        self._cursor_seq += 1
        cur = self.pg_v1_conn.cursor(f'v1_cur_{self._cursor_seq}', cursor_factory=factory)
        cur.itersize = 2000
        return cur

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def print_log(self, line):
        now = datetime.datetime.now()
        text = '[' + now.strftime('%d/%m/%Y %H:%M:%S') + '] ' + line
        if self._tui_active:
            top, middle, bottom = self._footer_lines()
            sys.stdout.write(f'\033[{self._FOOTER_HEIGHT - 1}A\033[J')
            sys.stdout.write(text + '\r\n')
            sys.stdout.write(top + '\r\n' + middle + '\r\n' + bottom)
        else:
            sys.stdout.write(text + '\n')
        sys.stdout.flush()

    def print_comment(self, line):
        self.write_sql('\n-----' + len(line) * '-')
        self.write_sql('\n-----' + line)
        self.write_sql('\n-----' + (len(line) * '-') + '\n\n')

    # ------------------------------------------------------------------
    # Escaping SQL
    # ------------------------------------------------------------------

    def sql_value(self, value):
        if value is None:
            return 'NULL'
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (int, float)):
            return str(value)
        escaped_value = str(value).replace("'", "''")
        if '\\' in escaped_value:
            return "E'" + escaped_value + "'"
        return "'" + escaped_value + "'"

    # ------------------------------------------------------------------
    # Lookups de referência (compartilhado — lê do V2)
    # ------------------------------------------------------------------

    def load_reference_map(
        self,
        cache_key: str,
        sql: str,
        *,
        key_normalizer=None,
        value_normalizer=None,
        skip_null_keys: bool = True,
    ) -> dict:
        """Carrega um dicionário de referência a partir do V2 e mantém em cache."""
        if cache_key in self._reference_maps:
            return self._reference_maps[cache_key]

        key_normalizer = key_normalizer or (lambda value: str(value).strip())
        value_normalizer = value_normalizer or (lambda value: value)

        ref_map = {}
        cur = self.pg_v2_conn.cursor()
        cur.execute(sql)

        for raw_key, raw_value in cur:
            if raw_key is None and skip_null_keys:
                continue
            key = key_normalizer(raw_key)
            if key is None and skip_null_keys:
                continue
            ref_map[key] = value_normalizer(raw_value)

        self._reference_maps[cache_key] = ref_map
        return ref_map

    # ------------------------------------------------------------------
    # Geração de triggers (compartilhado — lê do V2)
    # ------------------------------------------------------------------

    def set_trigger_commands(self):
        self.print_log('Gerando comandos de triggers para tabelas')
        cur = self.pg_v2_conn.cursor()
        cur.execute(
            "SELECT 'ALTER TABLE ' || schemaname || '.' || tablename || ' DISABLE TRIGGER ALL;' as disable_trigger, "
            "'ALTER TABLE ' || schemaname || '.' || tablename || ' ENABLE TRIGGER ALL;' as enable_trigger "
            "FROM pg_catalog.pg_tables "
            "WHERE schemaname <> 'information_schema' AND schemaname <> 'pg_catalog' "
            "ORDER BY schemaname, tablename;"
        )
        for row in cur:
            self.disable_triggers += row[0] + '\n'
            self.enable_triggers += row[1] + '\n'
        self.print_log('Comandos de trigger gerados')

    # ------------------------------------------------------------------
    # Métodos com padrão no-op — Acmes sobrescrevem conforme necessário
    # ------------------------------------------------------------------

    def set_indexes_commands(self):
        """Sobrescreva para configurar disable_indexes / enable_indexes."""
        pass

    def dump_ignoring_tables(self) -> list:
        """Retorna lista de tabelas a excluir do pg_dump (existem mas devem ser ignoradas)."""
        return []

    def dump_non_existing_tables(self) -> list:
        """Retorna lista de tabelas inexistentes no Acme (causam erro no pg_dump)."""
        return []

    def reset_sequences(self):
        """Gera setval para as sequences. Sobrescreva com as tabelas do Acme."""
        pass

    def run_initial(self):
        """Conteúdo do cmd_ini.sql. Sobrescreva para truncates, deletes e flags iniciais."""
        pass

    def run_inserts(self):
        """Conteúdo do cmd.sql. Sobrescreva com todos os insert_* e update_* do Acme."""
        pass

    # ------------------------------------------------------------------
    # pg_dump (lógica compartilhada, listas definidas pelo Acme)
    # ------------------------------------------------------------------

    def dump_tables(self):
        exclude_tables = [*self.dump_ignoring_tables(), *self.dump_non_existing_tables()]

        command = (
            'pg_dump -h {host} -p {port} -U {user} -a -b --column-inserts -f {file} {name} '
            '-E utf-8 --disable-triggers {excludes}'
        ).format(
            host=self.config['PG_V1_HOST'],
            port=self.config['PG_V1_PORT'],
            user=self.config['PG_V1_USER'],
            file=self.config['DUMP_FILENAME'],
            name=self.config['PG_V1_NAME'],
            excludes=''.join([f"-T '{t}' " for t in exclude_tables]),
        )

        os.environ['PGPASSWORD'] = self.config['PG_V1_PASS']
        self.print_log('Executando pg_dump de tabelas diferentes e com dados')

        if subprocess.call(shlex.split(command), shell=False) != 0:
            raise Exception('Comando de dump falhou')

        self.file.seek(0, 2)
        self.print_log('Tabelas ignoradas: ' + ', '.join(exclude_tables))
        self.print_log('Dump finalizado')

    # ------------------------------------------------------------------
    # Template de execução (não sobrescrever)
    # ------------------------------------------------------------------

    def run(self):
        try:
            self.print_log('----- Início do script de importação V1 - V2 -----')

            self.set_trigger_commands()
            self.set_indexes_commands()

            # cmd_ini.sql
            self._flush_writes()
            self.file = open(os.path.join(self._clientdir, self.config['INICIAL_FILENAME']), 'w+', encoding='utf-8')
            self.run_initial()

            # cmd.sql
            self._flush_writes()
            self.file = open(os.path.join(self._clientdir, self.config['PRINCIPAL_FILENAME']), 'w+', encoding='utf-8')
            self._reset_progress()
            self.run_inserts()
            self._finish_progress()

            # cmd_fim.sql
            self._flush_writes()
            self.file = open(os.path.join(self._clientdir, self.config['FINAL_FILENAME']), 'w+', encoding='utf-8')
            self.reset_sequences()
            self.print_comment('HABILITAR ÍNDICES DE TABELAS')
            self.write_sql(self.enable_indexes)
            self.print_comment('HABILITAR TRIGGERS DE TABELAS')
            self.write_sql(self.enable_triggers)
            self._flush_writes()

            self.print_log('----- Fim do script de importação V1 - V2 -----')

        except Exception as e:
            self.print_log('[ERRO] ' + str(e))
            self.print_log(traceback.format_exc())
