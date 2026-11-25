"""Test doubles shared by the applier and report tests."""

import json
import os

SETUP = '00_setup.sql'
PREPARE = '01_prepare_target.sql'
DUMP = '02_dump_compatible_tables.sql'
LOAD = '03_load_transformed_data.sql'
FINALIZE = '04_finalize_target.sql'

DEFAULT_FILES = {
    SETUP: '-- abertura\n',
    PREPARE: 'TRUNCATE a;\n',
    DUMP: "SELECT pg_catalog.set_config('search_path', '', false);\nINSERT INTO public.a VALUES (1);\n",
    LOAD: 'INSERT INTO b VALUES (1);\nINSERT INTO b VALUES (2);\n',
    FINALIZE: "SELECT pg_catalog.setval('a_f_id_seq', 1);\n",
}


def make_run(tmpdir, files=None, status='success', debug=False):
    run_dir = os.path.join(tmpdir, 'runs', 'Acme', '20261124-100000')
    os.makedirs(os.path.join(run_dir, 'sql'))
    manifest = {
        'client': 'Acme',
        'status': status,
        'debug': {'enabled': debug, 'limit': 100 if debug else None},
    }
    with open(os.path.join(run_dir, 'manifest.json'), 'w', encoding='utf-8') as manifest_file:
        json.dump(manifest, manifest_file)

    for name, content in (DEFAULT_FILES if files is None else files).items():
        with open(os.path.join(run_dir, 'sql', name), 'w', encoding='utf-8') as sql_file:
            sql_file.write(content)
    return run_dir


class FakeDatabaseError(Exception):
    def __init__(self, message, pgcode=None):
        super().__init__(message)
        self.pgcode = pgcode


class FakeConnection:
    """DB-API connection that records statements and simulates a transaction.

    ``failures`` maps a fragment of SQL to ``(message, pgcode)``. A failing
    statement leaves the transaction aborted, as in PostgreSQL: anything but
    ``ROLLBACK TO SAVEPOINT`` fails until the transaction ends. ``pending``
    holds what the open transaction applied and ``committed`` what COMMIT
    kept. ``commit()`` and ``rollback()`` appear in ``log`` as ``<commit>``
    and ``<rollback>``.
    """

    def __init__(
        self,
        failures=None,
        interrupt_on=None,
        commit_error=None,
        standard_conforming_strings='on',
    ):
        self.failures = failures or {}
        self.interrupt_on = interrupt_on
        self.commit_error = commit_error
        self.standard_conforming_strings = standard_conforming_strings
        self.log = []
        self.pending = []
        self.committed = []
        self.savepoints = []
        self.aborted = False
        self.autocommit = True
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def get_dsn_parameters(self):
        return {'host': 'target-db', 'port': '5432', 'dbname': 'tenant', 'user': 'tenant', 'password': 'segredo'}

    def commit(self):
        self.log.append('<commit>')
        if self.commit_error:
            self._end_transaction()
            raise FakeDatabaseError(self.commit_error, '23503')
        self.committed.extend(self.pending)
        self._end_transaction()

    def rollback(self):
        self.log.append('<rollback>')
        self._end_transaction()

    def close(self):
        self.closed = True

    def statements(self):
        """Log without the bookkeeping statements of the applier."""

        return [sql for sql in self.log if not sql.startswith(('SHOW ', 'SAVEPOINT ', 'RELEASE ', 'ROLLBACK TO '))]

    def execute(self, sql):
        self.log.append(sql)
        if self.aborted and not sql.startswith('ROLLBACK TO SAVEPOINT '):
            raise FakeDatabaseError('current transaction is aborted, commands ignored until end of transaction block', '25P02')

        if sql.startswith('SAVEPOINT '):
            self.savepoints.append((sql.split()[-1], len(self.pending)))
        elif sql.startswith('ROLLBACK TO SAVEPOINT '):
            index = self._savepoint_index(sql.split()[-1])
            self.pending = self.pending[:self.savepoints[index][1]]
            del self.savepoints[index + 1:]
            self.aborted = False
        elif sql.startswith('RELEASE SAVEPOINT '):
            del self.savepoints[self._savepoint_index(sql.split()[-1]):]
        elif sql == 'RESET ALL' or sql.startswith('SHOW '):
            pass
        else:
            self._run(sql)

    def _run(self, sql):
        if self.interrupt_on and self.interrupt_on in sql:
            raise KeyboardInterrupt
        for fragment, (message, pgcode) in self.failures.items():
            if fragment in sql:
                self.aborted = True
                raise FakeDatabaseError(message, pgcode)
        self.pending.append(sql)

    def _savepoint_index(self, name):
        for index in range(len(self.savepoints) - 1, -1, -1):
            if self.savepoints[index][0] == name:
                return index
        raise FakeDatabaseError(f'savepoint "{name}" does not exist', '3B001')

    def _end_transaction(self):
        self.pending = []
        self.savepoints = []
        self.aborted = False


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._row = None

    def execute(self, sql):
        self.connection.execute(sql)
        if sql == 'SHOW standard_conforming_strings':
            self._row = (self.connection.standard_conforming_strings,)

    def fetchone(self):
        return self._row


class Clock:
    """Returns the given datetimes in order, repeating the last one."""

    def __init__(self, *moments):
        self.moments = list(moments)

    def __call__(self):
        if len(self.moments) > 1:
            return self.moments.pop(0)
        return self.moments[0]
