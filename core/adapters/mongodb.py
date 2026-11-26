"""MongoDB adapter, as source and as target.

Connection keys: ``<PREFIX>_URI`` (required, kept out of the manifest) and
``<PREFIX>_NAME`` with the database, optional when the URI names it. The
driver (``pymongo``, which brings ``bson``) is imported only when a
connection opens or an artifact is written or read.

Source: a ``SourceQuery`` becomes ``find`` (projection, equality filter, sort,
limit); a ``NativeQuery`` or a ``NativeField`` becomes an aggregation
pipeline. ``_id`` comes only when asked for.

Target: operations become lines of relaxed Extended JSON in ``.jsonl``
artifacts, which keep dates, ``Decimal128`` and binary data. The session
applies them in one transaction, grouping consecutive inserts into the same
collection in ``insert_many`` batches. MongoDB has transactions only in a
replica set or behind ``mongos``, and no savepoints.
"""

import datetime
import decimal
import uuid

from core.application import Capabilities, Command, DeferredCommandError
from core.artifacts import ArtifactFormat
from core.connections import ConnectionConfigError
from core.operations import (
    Clear,
    Comment,
    Insert,
    Native,
    ResetIdentity,
    ResumeConstraints,
    SuspendConstraints,
    ValueSerializationError,
)
from core.queries import (
    NativeAdapterMismatchError,
    NativeField,
    NativeQuery,
    SourceQuery,
    UnsupportedQueryError,
    check_native,
)
from core.references import TargetReadClosedError
from core.sql import SqlLiteral
from core.versions import check_server_version, parse_version

NAME = 'mongodb'
SUPPORTED_VERSIONS = (4, 4)
REQUIRED_PARAMETERS = ('uri',)
INSERT_BATCH = 1000
# Operations a .jsonl line may carry. Native operations use the same shapes.
OPERATIONS = ('insert', 'delete_many', 'update_many', 'command')
_INT64 = (-(2 ** 63), 2 ** 63 - 1)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def _connect(config, role: str):
    """MongoClient, database and server version of a ConnectionConfig, version checked."""

    import pymongo

    config.require(*REQUIRED_PARAMETERS)
    client = pymongo.MongoClient(
        config.get('uri'),
        tz_aware=True,
        uuidRepresentation='standard',
        serverSelectionTimeoutMS=int(config.get('timeout_ms', '10000')),
    )
    try:
        version = client.server_info().get('version')
        check_server_version(config.prefix, NAME, role, parse_version(version), SUPPORTED_VERSIONS)
        database = _database(client, config)
    except Exception:
        client.close()
        raise
    return client, database, version


def _database(client, config):
    name = config.get('name')
    if name:
        return client[name]
    try:
        return client.get_default_database()
    except Exception as exc:
        raise ConnectionConfigError(
            f'{config.prefix}_NAME não definido e a URI de {config.prefix} não nomeia o banco.'
        ) from exc


def _json():
    from bson import json_util
    from bson.binary import UuidRepresentation
    from bson.json_util import JSONMode, JSONOptions

    return json_util, JSONOptions(
        json_mode=JSONMode.RELAXED,
        uuid_representation=UuidRepresentation.STANDARD,
        tz_aware=True,
    )


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class MongoSourceAdapter:
    """MongoDB source: one entity is one collection, one row is one document."""

    name = NAME
    supported_versions = SUPPORTED_VERSIONS
    required_parameters = REQUIRED_PARAMETERS

    def __init__(self, database, client=None, server_version=None):
        self.database = database
        self.client = client
        self.server_version = server_version

    @classmethod
    def connect(cls, config):
        client, database, version = _connect(config, 'source')
        return cls(database, client, version)

    def read(self, query):
        # Build now, so a refused query fails at the call and not at the first row.
        cursor = self._cursor(query)
        fields = self._plain_fields(query)
        return (_row(document, fields) for document in cursor)

    def count(self, query):
        if isinstance(query, SourceQuery) and not _native_fields(query):
            if query.limit == 0:
                return 0
            options = {'limit': query.limit} if query.limit else {}
            return self.database[query.entity].count_documents(dict(query.where), **options)
        collection, pipeline = self._pipeline(query)
        result = list(self.database[collection].aggregate([*pipeline, {'$count': 'total'}]))
        return result[0]['total'] if result else 0

    def close(self):
        if self.client is not None:
            self.client.close()

    def _cursor(self, query):
        if isinstance(query, SourceQuery) and not _native_fields(query):
            cursor = self.database[query.entity].find(dict(query.where), _projection(query.fields))
            if query.order_by:
                cursor = cursor.sort([(field, 1) for field in query.order_by])
            if query.limit is not None:
                cursor = cursor.limit(query.limit) if query.limit else iter(())
            return cursor
        collection, pipeline = self._pipeline(query)
        return self.database[collection].aggregate(pipeline)

    def _pipeline(self, query):
        if isinstance(query, NativeQuery):
            check_native(query, self.name)
            native = query.query
            if not isinstance(native, dict) or 'collection' not in native or not isinstance(native.get('pipeline'), list):
                raise UnsupportedQueryError(
                    "A NativeQuery do adapter mongodb deve ser {'collection': ..., 'pipeline': [...]}."
                )
            pipeline = list(native['pipeline'])
            if query.limit is not None:
                pipeline.append({'$limit': query.limit})
            return native['collection'], pipeline
        if isinstance(query, SourceQuery):
            for item in query.fields:
                check_native(item, self.name)
            pipeline = []
            if query.where:
                pipeline.append({'$match': dict(query.where)})
            if query.order_by:
                pipeline.append({'$sort': {field: 1 for field in query.order_by}})
            if query.limit is not None:
                pipeline.append({'$limit': query.limit})
            project = {'_id': 0}
            for item in query.fields:
                if isinstance(item, NativeField):
                    project[item.alias] = item.expression
                else:
                    project[item] = 1
                    if item == '_id':
                        project['_id'] = 1
            pipeline.append({'$project': project})
            return query.entity, pipeline
        raise UnsupportedQueryError(f'O adapter mongodb não lê {type(query).__name__}.')

    @staticmethod
    def _plain_fields(query):
        if isinstance(query, SourceQuery):
            return [field for field in query.fields if isinstance(field, str)]
        return []


def _native_fields(query):
    return [field for field in query.fields if isinstance(field, NativeField)]


def _projection(fields):
    if not fields:
        return {'_id': 0}
    projection = {field: 1 for field in fields}
    projection.setdefault('_id', 0)
    return projection


def _row(document, fields):
    """Document as a row: Decimal128 becomes Decimal and a dotted field gets its own key."""

    row = {key: _from_bson(value) for key, value in document.items()}
    for field in fields:
        if '.' in field:
            row[field] = _from_bson(_path(document, field))
    return row


def _path(document, dotted):
    value = document
    for part in dotted.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _from_bson(value):
    # Decimal128 has to_decimal(); checking the method keeps bson out of the import.
    if hasattr(value, 'to_decimal') and not isinstance(value, decimal.Decimal):
        return value.to_decimal()
    if isinstance(value, dict):
        return {key: _from_bson(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_bson(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Target
# ---------------------------------------------------------------------------


class MongoTargetAdapter:
    """MongoDB target: operations as Extended JSON lines, applied in a transaction."""

    name = NAME
    artifact_format = ArtifactFormat('jsonl', 'jsonl')
    # Declared maximum. A session outside a replica set reports no transactions.
    capabilities = Capabilities(transactions=True, savepoints=False, suspend_constraints=False)
    required_parameters = REQUIRED_PARAMETERS
    supported_versions = SUPPORTED_VERSIONS

    def __init__(self, database=None, client=None, server_version=None):
        self.database = database
        self.client = client
        self.server_version = server_version
        self._reads_closed = False

    @classmethod
    def connect(cls, config):
        client, database, version = _connect(config, 'target')
        return cls(database, client, version)

    def close(self):
        if self.client is not None:
            self.client.close()

    def session(self):
        return MongoSession(self.client, self.database)

    # Reading ---------------------------------------------------------------

    def read(self, query):
        if self._reads_closed:
            raise TargetReadClosedError(
                'Target reads are closed: the target adapter cannot query the target '
                'after the reference maps are loaded'
            )
        return MongoSourceAdapter(self.database).read(query)

    def close_reads(self):
        # No read transaction to end: reads are plain finds.
        self._reads_closed = True

    # Rendering -------------------------------------------------------------

    def render(self, operation) -> list:
        if isinstance(operation, Insert):
            document = {column: _to_bson(operation.entity, column, value) for column, value in operation.values.items()}
            return [self._line({'op': 'insert', 'collection': operation.entity, 'document': document})]
        if isinstance(operation, Clear):
            # delete_many keeps the indexes and validators of the collection.
            return [self._line({'op': 'delete_many', 'collection': entity, 'filter': {}}) for entity in operation.entities]
        if isinstance(operation, (SuspendConstraints, ResumeConstraints, ResetIdentity, Comment)):
            return []
        if isinstance(operation, Native):
            if operation.adapter != self.name:
                raise NativeAdapterMismatchError('A operação Native', operation.adapter, self.name)
            _check_operation(operation.payload)
            return [self._line(operation.payload)]
        raise TypeError(f'O adapter mongodb não renderiza {type(operation).__name__}.')

    @staticmethod
    def _line(document) -> str:
        json_util, options = _json()
        return json_util.dumps(document, json_options=options) + '\n'


def _check_operation(payload):
    if not isinstance(payload, dict) or payload.get('op') not in OPERATIONS:
        raise ValueError(f"Operação MongoDB deve ser um dict com 'op' em {', '.join(OPERATIONS)}; recebeu {payload!r}.")


def _to_bson(entity: str, column: str, value):
    """Python value as a BSON value, from a closed list of types."""

    try:
        return _convert(value)
    except TypeError as exc:
        raise ValueSerializationError(entity, column, type(value).__name__, str(exc)) from exc


def _convert(value):
    from bson.decimal128 import Decimal128

    if value is None or isinstance(value, (bool, str, float)):
        return value
    if isinstance(value, int):
        if not _INT64[0] <= value <= _INT64[1]:
            raise TypeError('integer does not fit in 64 bits, the largest BSON integer')
        return value
    if isinstance(value, decimal.Decimal):
        return Decimal128(value)
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        # BSON has no date without time.
        return datetime.datetime(value.year, value.month, value.day, tzinfo=datetime.timezone.utc)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, dict):
        return {str(key): _convert(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_convert(item) for item in value]
    if isinstance(value, SqlLiteral):
        raise TypeError('SqlLiteral is SQL, which a MongoDB target does not run')
    if type(value).__module__.startswith('bson'):
        return value
    raise TypeError(f'type {type(value).__name__} is not supported by the MongoDB adapter')


# ---------------------------------------------------------------------------
# Application session
# ---------------------------------------------------------------------------


class MongoSession:
    """One client session and one transaction for an application."""

    def __init__(self, client, database):
        self.client = client
        self.database = database
        self._session = None
        self._batch = []
        self._batch_collection = None
        hello = client.admin.command('hello')
        # Transactions need a replica set, or a sharded cluster behind mongos.
        transactions = bool(hello.get('setName')) or hello.get('msg') == 'isdbgrid'
        self.capabilities = Capabilities(transactions=transactions, savepoints=False, suspend_constraints=False)
        self.transaction_limit_seconds = _transaction_limit(client)

    @property
    def pending(self) -> int:
        return len(self._batch)

    def describe(self) -> dict:
        description = {'adapter': NAME, 'database': self.database.name}
        if self.transaction_limit_seconds is not None:
            description['transaction_limit_seconds'] = self.transaction_limit_seconds
        return description

    def begin(self):
        self._session = self.client.start_session()
        self._session.start_transaction()

    def commands(self, path: str, dry_run: bool):
        json_util, options = _json()
        with open(path, encoding='utf-8') as artifact:
            for number, line in enumerate(artifact, 1):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json_util.loads(text, json_options=options)
                    _check_operation(payload)
                except Exception as exc:
                    yield Command(text, number, error=f'linha inválida: {exc}')
                    continue
                yield Command(text, number, payload=payload)

    def execute(self, command: Command):
        payload = command.payload
        if payload['op'] == 'insert':
            if self._batch and self._batch_collection != payload['collection']:
                self.flush()
            self._batch_collection = payload['collection']
            self._batch.append(payload['document'])
            if len(self._batch) >= INSERT_BATCH:
                self.flush()
            return
        # Anything else runs after the inserts that came before it.
        self.flush()
        self._run(payload)

    def flush(self):
        if not self._batch:
            return
        batch, self._batch = self._batch, []
        try:
            self.database[self._batch_collection].insert_many(batch, ordered=True, session=self._session)
        except Exception as exc:
            raise DeferredCommandError(_failed_index(exc), exc) from exc

    def _run(self, payload):
        collection = self.database[payload['collection']] if 'collection' in payload else None
        op = payload['op']
        if op == 'delete_many':
            collection.delete_many(payload.get('filter', {}), session=self._session)
        elif op == 'update_many':
            collection.update_many(payload.get('filter', {}), payload['update'], session=self._session)
        elif op == 'command':
            self.database.command(payload['command'], session=self._session)
        else:
            collection.insert_one(payload['document'], session=self._session)

    def end_artifact(self):
        pass

    def commit(self):
        self._session.commit_transaction()

    def rollback(self):
        self._batch = []
        if self._session is not None and self._session.in_transaction:
            self._session.abort_transaction()

    def error_code(self, exc: BaseException):
        details = getattr(exc, 'details', None) or {}
        write_errors = details.get('writeErrors') if isinstance(details, dict) else None
        if write_errors:
            return write_errors[0].get('code')
        return getattr(exc, 'code', None)

    def close(self):
        if self._session is not None:
            self._session.end_session()
        self.client.close()


def _failed_index(exc) -> int:
    details = getattr(exc, 'details', None) or {}
    write_errors = details.get('writeErrors') if isinstance(details, dict) else None
    return write_errors[0].get('index', 0) if write_errors else 0


def _transaction_limit(client):
    """transactionLifetimeLimitSeconds of the server: a transaction older than it is aborted."""

    try:
        result = client.admin.command({'getParameter': 1, 'transactionLifetimeLimitSeconds': 1})
    except Exception:
        # Reading server parameters needs a privilege the user may not have.
        return None
    return result.get('transactionLifetimeLimitSeconds')
