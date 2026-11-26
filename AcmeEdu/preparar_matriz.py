"""Preenche as bases da matriz de prova a partir da V1 em PostgreSQL.

Uso, dentro de um container na rede hermes-net com os três drivers:

    python3 AcmeEdu/preparar_matriz.py [mongo] [fb3] [fb5]

Sem argumentos, prepara todas. Os dados são os da V1 em PostgreSQL 9.6
(acmeedu-v1), copiados tabela por tabela, para todos os caminhos da matriz
migrarem os mesmos alunos, professores e matrículas:

- mongo: a V1 em MongoDB (banco acmeedu_v1, uma coleção por tabela) e, no
  banco acmeedu_v2, a coleção student_statuses lida da V2 em PostgreSQL, que o
  mapa de referência do script precisa;
- fb3 e fb5: a V1 em Firebird 3 e 5, com as mesmas tabelas e colunas.

Este é um script de apoio da prova: ele fala com os drivers direto.
"""

import datetime
import os
import sys
from decimal import Decimal

TABLES = ['tb_curso', 'tb_professor', 'tb_disciplina', 'tb_turma', 'tb_aluno', 'tb_matricula', 'holidays']
POSTGRES = {'user': 'acmeedu', 'password': 'acmeedu'}
MONGO_URI = os.environ.get('MATRIZ_MONGO_URI', 'mongodb://acmeedu-mongo:27017/?replicaSet=rs0')
FIREBIRD = {
    'fb3': os.environ.get('MATRIZ_FB3_HOST', 'acmeedu-fb3'),
    'fb5': os.environ.get('MATRIZ_FB5_HOST', 'acmeedu-fb5'),
}
FIREBIRD_DATABASE = '/var/lib/firebird/data/acmeedu_v1.fdb'

# Tipos da V1 em PostgreSQL para o Firebird. O text vira VARCHAR, que o
# adapter devolve como texto, sem BLOB.
FIREBIRD_TYPES = {
    'integer': 'INTEGER',
    'character varying': 'VARCHAR({length})',
    'character': 'CHAR({length})',
    'text': 'VARCHAR(4000)',
    'timestamp without time zone': 'TIMESTAMP',
    'date': 'DATE',
    'numeric': 'NUMERIC({precision},{scale})',
}


def read_postgres(host, database):
    import psycopg2

    connection = psycopg2.connect(host=host, dbname=database, **POSTGRES)
    try:
        cursor = connection.cursor()
        tables = {}
        for table in TABLES if database == 'acmeedu_v1' else ['student_statuses']:
            cursor.execute(
                'SELECT column_name, data_type, character_maximum_length, numeric_precision, numeric_scale '
                'FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position',
                ('public', table),
            )
            columns = cursor.fetchall()
            names = [column[0] for column in columns]
            cursor.execute(f'SELECT {", ".join(names)} FROM {table} ORDER BY 1')
            tables[table] = (columns, [dict(zip(names, row)) for row in cursor.fetchall()])
        return tables
    finally:
        connection.close()


def fill_mongo(v1, statuses):
    import pymongo
    from bson.decimal128 import Decimal128

    client = pymongo.MongoClient(MONGO_URI)
    try:
        source = client['acmeedu_v1']
        for table, (_, rows) in v1.items():
            source.drop_collection(table)
            documents = [{key: _bson_value(value, Decimal128) for key, value in row.items()} for row in rows]
            source[table].insert_many(documents)
            print(f'mongo acmeedu_v1.{table}: {len(documents)} documentos')

        target = client['acmeedu_v2']
        for collection in target.list_collection_names():
            target.drop_collection(collection)
        target['student_statuses'].insert_many(statuses)
        print(f'mongo acmeedu_v2.student_statuses: {len(statuses)} documentos')
    finally:
        client.close()


def _bson_value(value, decimal128):
    if isinstance(value, Decimal):
        return decimal128(value)
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        # BSON não tem data sem hora.
        return datetime.datetime(value.year, value.month, value.day)
    return value


def fill_firebird(name, host, v1):
    import firebirdsql

    connection = firebirdsql.connect(
        host=host, database=FIREBIRD_DATABASE, user='acmeedu', password='acmeedu', charset='UTF8',
    )
    try:
        cursor = connection.cursor()
        for table, (columns, rows) in v1.items():
            definitions = []
            for column, data_type, length, precision, scale in columns:
                sql_type = FIREBIRD_TYPES[data_type].format(length=length, precision=precision, scale=scale)
                definitions.append(f'{column} {sql_type}')
            cursor.execute(f'RECREATE TABLE {table} ({", ".join(definitions)})')
            connection.commit()
            names = [column[0] for column in columns]
            # Preparado uma vez: o Firebird 5 recusa preparar o mesmo comando milhares de vezes.
            insert = cursor.prep(f'INSERT INTO {table} ({", ".join(names)}) VALUES ({", ".join("?" for _ in names)})')
            for number, row in enumerate(rows, 1):
                cursor.execute(insert, [row[column] for column in names])
                if number % 500 == 0:
                    connection.commit()
            connection.commit()
            print(f'{name} {table}: {len(rows)} linhas')
    finally:
        connection.close()


def main(targets):
    targets = targets or ['mongo', *FIREBIRD]
    v1 = read_postgres('acmeedu-v1', 'acmeedu_v1')
    if 'mongo' in targets:
        statuses = read_postgres('acmeedu-v2', 'acmeedu_v2')['student_statuses'][1]
        fill_mongo(v1, statuses)
    for name, host in FIREBIRD.items():
        if name in targets:
            fill_firebird(name, host, v1)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
