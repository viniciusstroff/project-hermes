"""
Estratégias de campo para migrações.

Cada estratégia representa a transformação de UM campo: como selecioná-lo
da V1 e qual valor inserir na V2.

Interface comum:
  - select_columns  → list[str]  colunas a incluir no SELECT (Fixed retorna [])
  - insert_col      → str        nome da coluna no INSERT
  - value(row)      → str        valor SQL-escaped pronto para VALUES(...)
"""
from abc import ABC, abstractmethod
import re
import datetime


def sql_value(value) -> str:
    """Escapa um valor Python para uso direto em SQL."""
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    if '\\' in escaped:
        return "E'" + escaped + "'"
    return "'" + escaped + "'"


class FieldStrategy(ABC):
    """Contrato explícito para estratégias de campo."""

    @property
    @abstractmethod
    def select_columns(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def insert_col(self):
        raise NotImplementedError

    @abstractmethod
    def value(self, row):
        raise NotImplementedError


class Copy(FieldStrategy):
    """Copia um campo com o mesmo nome: v1.f_id → v2.f_id"""

    def __init__(self, name: str):
        self.name = name

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.name

    def value(self, row):
        return sql_value(row[self.name])


class Rename(FieldStrategy):
    """Renomeia o campo: v1.f_active → v2.f_status"""

    def __init__(self, v1_name: str, v2_name: str):
        self.v1_name = v1_name
        self.v2_name = v2_name

    @property
    def select_columns(self):
        return [self.v1_name]

    @property
    def insert_col(self):
        return self.v2_name

    def value(self, row):
        return sql_value(row[self.v1_name])


class Fixed(FieldStrategy):
    """Valor literal fixo — não vem do SELECT.
    Ex: Fixed('f_create_user', 814) injeta 814 em todas as linhas."""

    def __init__(self, col: str, value):
        self.col = col
        self._value = value

    @property
    def select_columns(self):
        return []

    @property
    def insert_col(self):
        return self.col

    def value(self, row):
        return sql_value(self._value)


class SqlExpression(FieldStrategy):
    """Expressão SQL arbitrária no SELECT."""

    def __init__(self, expression: str, col: str):
        self.expression = expression
        self.col = col

    @property
    def select_columns(self):
        return [f'{self.expression} AS {self.col}']

    @property
    def insert_col(self):
        return self.col

    def value(self, row):
        return sql_value(row[self.col])


class Lookup(FieldStrategy):
    """Resolve um valor via dicionário — útil para varchar V1 → FK inteiro V2.
    Ex: Lookup('f_state', {'SP': 25, 'RJ': 19}, fallback=None)
    Se v2_name for omitido, usa o mesmo nome do campo V1."""

    def __init__(
        self,
        name: str,
        lookup_dict: dict,
        v2_name: str = None,
        fallback=None,
        key_normalizer=None,
    ):
        self.name = name
        self.lookup_dict = lookup_dict
        self.v2_name = v2_name or name
        self.fallback = fallback
        self.key_normalizer = key_normalizer or (lambda value: str(value).strip())

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.v2_name

    def value(self, row):
        raw = row[self.name]
        if raw is None:
            return sql_value(self.fallback)
        key = self.key_normalizer(raw)
        resolved = self.lookup_dict.get(key, self.fallback)
        return sql_value(resolved)


class Transform(FieldStrategy):
    """Transforma um valor Python via callback antes de serializar para SQL."""

    def __init__(self, name: str, transform_fn, v2_name: str = None):
        self.name = name
        self.transform_fn = transform_fn
        self.v2_name = v2_name or name

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.v2_name

    def value(self, row):
        return sql_value(self.transform_fn(row[self.name], row))


class RegexClean(FieldStrategy):
    """Remove ou substitui padrões no valor via re.sub.
    Ex: RegexClean('f_text', r'[^\\w\\s]') strip caracteres inválidos."""

    def __init__(self, name: str, pattern: str, replacement: str = ''):
        self.name = name
        self.pattern = pattern
        self.replacement = replacement

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.name

    def value(self, row):
        raw = row[self.name]
        if raw is None:
            return 'NULL'
        cleaned = re.sub(self.pattern, self.replacement, str(raw))
        escaped = cleaned.replace("'", "''").replace('\\', '')
        return "E'" + escaped + "'"


class CpfClean(RegexClean):
    """Remove formatação de CPF — pontos, traços e barras."""

    def __init__(self, name: str, v2_name: str = None):
        super().__init__(name, r'[^\d]', '')
        self._v2_name = v2_name

    @property
    def insert_col(self):
        return self._v2_name or self.name


class PhoneClean(RegexClean):
    """Remove formatação de telefone — parênteses, traços, espaços."""

    def __init__(self, name: str, v2_name: str = None):
        super().__init__(name, r'[^\d+]', '')
        self._v2_name = v2_name

    @property
    def insert_col(self):
        return self._v2_name or self.name


class DateConvert(FieldStrategy):
    """Converte formato de data.
    Ex: DateConvert('f_nascimento') converte DD/MM/YYYY → YYYY-MM-DD
    Ex: DateConvert('f_nascimento', v2_name='f_birthday') converte e renomeia o campo."""

    def __init__(self, name: str, from_fmt: str = '%d/%m/%Y', to_fmt: str = '%Y-%m-%d', v2_name: str = None):
        self.name = name
        self.from_fmt = from_fmt
        self.to_fmt = to_fmt
        self._v2_name = v2_name

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self._v2_name or self.name

    def value(self, row):
        raw = row[self.name]
        if raw is None or str(raw).strip() == '':
            return 'NULL'
        try:
            parsed = datetime.datetime.strptime(str(raw).strip(), self.from_fmt)
            return "'" + parsed.strftime(self.to_fmt) + "'"
        except ValueError:
            return 'NULL'


class EmailExtract(FieldStrategy):
    """Extrai o primeiro e-mail válido do valor ou retorna NULL."""

    def __init__(self, name: str):
        self.name = name

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.name

    def value(self, row):
        raw = row[self.name]
        if raw is None or str(raw).strip() == '':
            return 'NULL'
        match = re.search(r'[\w.\-+]+@[\w.\-]+', str(raw))
        if match:
            return "'" + match.group(0).replace("'", "''") + "'"
        return 'NULL'
