"""
Estratégias de campo para migrações.

Cada estratégia representa a transformação de UM campo: como selecioná-lo
da V1 e qual valor Python inserir na V2. Nenhuma estratégia gera SQL: quem
serializa o valor é o adapter de destino, chamado pela migração.

Interface comum:
  - select_columns   → list       campos a ler da origem (Fixed retorna [])
  - insert_col       → str        nome da coluna no INSERT
  - raw_value(row)   → any        valor Python que vai para o destino
  - resolve(row)     → any        o mesmo valor, ou um LostValue quando a estratégia
                                  não conseguiu manter o valor da origem (opcional)

Código de um banco específico continua possível em dois pontos, sempre preso
a um adapter:
  - NativeField (core.queries), campo calculado na origem na linguagem dela;
  - um raw_value que devolve SqlLiteral, que o adapter de destino copia sem mexer.
"""
from abc import ABC, abstractmethod
import datetime
import re

from core.metrics import EMAIL_NOT_FOUND, INVALID_DATE, LOOKUP_MISS, LostValue, is_blank
from core.references import LoadedReferenceMap

REQUIRED_FIELD_ATTRIBUTES = ('select_columns', 'insert_col', 'raw_value')
# Marks a key missing from a lookup map, where None may be a valid value.
_MISSING = object()


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
    def raw_value(self, row):
        raise NotImplementedError

    def resolve(self, row):
        """Valor para o destino, ou LostValue quando o valor da origem se perdeu.

        A migração registra a perda no relatório da geração e grava o valor do
        LostValue. O padrão é o próprio raw_value, sem perda.
        """
        return self.raw_value(row)


def validate_field_strategies(fields):
    """Recusa objetos de campo que não seguem o contrato de estratégia."""

    for field in fields:
        missing = [attr for attr in REQUIRED_FIELD_ATTRIBUTES if not hasattr(field, attr)]
        if missing:
            raise TypeError(f'Invalid field strategy {field!r}: missing {", ".join(missing)}')


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

    def raw_value(self, row):
        return row[self.name]


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

    def raw_value(self, row):
        return row[self.v1_name]


class Fixed(FieldStrategy):
    """Valor fixo, que não vem do SELECT.
    Ex: Fixed('f_create_user', 814) injeta 814 em todas as linhas.
    Ex: Fixed('f_created_at', SqlLiteral('CURRENT_TIMESTAMP')) injeta SQL do destino."""

    def __init__(self, col: str, value):
        self.col = col
        self._value = value

    @property
    def select_columns(self):
        return []

    @property
    def insert_col(self):
        return self.col

    def raw_value(self, row):
        return self._value


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
        if key_normalizer is None and isinstance(lookup_dict, LoadedReferenceMap):
            # The loaded map already knows how its keys were normalized.
            key_normalizer = lookup_dict.normalize_key
        self.key_normalizer = key_normalizer or (lambda value: str(value).strip())

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.v2_name

    def raw_value(self, row):
        value = self.resolve(row)
        return value.value if isinstance(value, LostValue) else value

    def resolve(self, row):
        raw = row[self.name]
        if raw is None:
            return self.fallback
        key = self.key_normalizer(raw)
        value = self.lookup_dict.get(key, _MISSING)
        if value is not _MISSING:
            return value
        if is_blank(raw):
            return self.fallback
        name = self.lookup_dict.name if isinstance(self.lookup_dict, LoadedReferenceMap) else None
        return LostValue(self.fallback, LOOKUP_MISS, key, reference_map=name)


class Transform(FieldStrategy):
    """Transforma um valor Python via callback antes de ir para o destino."""

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

    def raw_value(self, row):
        return self.transform_fn(row[self.name], row)


class ColumnStrategy(FieldStrategy):
    """Base das estratégias que leem uma coluna e aplicam uma regra ao valor."""

    def __init__(self, name: str, v2_name: str = None):
        self.name = name
        self.v2_name = v2_name or name

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.v2_name

    def raw_value(self, row):
        return self.convert(row[self.name])

    @abstractmethod
    def convert(self, value):
        """Aplica a regra da estratégia ao valor lido da origem."""


class RegexClean(ColumnStrategy):
    """Remove ou substitui padrões no valor via re.sub.
    Ex: RegexClean('f_text', r'[^\\w\\s]') remove caracteres que não são letra, número ou espaço."""

    def __init__(self, name: str, pattern: str, replacement: str = '', v2_name: str = None):
        super().__init__(name, v2_name)
        self.pattern = pattern
        self.replacement = replacement

    def convert(self, value):
        if value is None:
            return None
        return re.sub(self.pattern, self.replacement, str(value))


class CpfClean(RegexClean):
    """Remove formatação de CPF — pontos, traços e barras."""

    def __init__(self, name: str, v2_name: str = None):
        super().__init__(name, r'[^\d]', '', v2_name=v2_name)


class PhoneClean(RegexClean):
    """Remove formatação de telefone — parênteses, traços, espaços."""

    def __init__(self, name: str, v2_name: str = None):
        super().__init__(name, r'[^\d+]', '', v2_name=v2_name)


class NullIfBlank(ColumnStrategy):
    """Troca texto vazio ou só com espaços por None. Substitui NULLIF(col, '')."""

    def convert(self, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value


class Truncate(ColumnStrategy):
    """Corta texto em `length` caracteres. Substitui substring(col FROM 1 FOR n)."""

    def __init__(self, name: str, length: int, v2_name: str = None):
        if length < 0:
            raise ValueError('length must not be negative')
        super().__init__(name, v2_name)
        self.length = length

    def convert(self, value):
        if isinstance(value, str):
            return value[:self.length]
        return value


class Upper(ColumnStrategy):
    """Texto em maiúsculas pela regra do Python. Substitui upper(col)."""

    def convert(self, value):
        return value.upper() if isinstance(value, str) else value


class Lower(ColumnStrategy):
    """Texto em minúsculas pela regra do Python. Substitui lower(col)."""

    def convert(self, value):
        return value.lower() if isinstance(value, str) else value


class Default(ColumnStrategy):
    """Troca None por um valor padrão. Substitui COALESCE(col, valor)."""

    def __init__(self, name: str, value, v2_name: str = None):
        super().__init__(name, v2_name)
        self.default = value

    def convert(self, value):
        return self.default if value is None else value


class DateConvert(ColumnStrategy):
    """Converte texto em data.
    Ex: DateConvert('f_nascimento') lê DD/MM/YYYY e devolve datetime.date.
    Ex: DateConvert('f_criado', '%d/%m/%Y %H:%M', as_datetime=True) devolve datetime.datetime.
    Valor vazio ou inválido vira None. O formato gravado no destino é do adapter."""

    # v2_name só nomeado: a assinatura antiga tinha to_fmt na terceira posição,
    # e um terceiro argumento posicional não pode virar nome de coluna em silêncio.
    def __init__(self, name: str, from_fmt: str = '%d/%m/%Y', *, v2_name: str = None, as_datetime: bool = False):
        super().__init__(name, v2_name)
        self.from_fmt = from_fmt
        self.as_datetime = as_datetime

    def convert(self, value):
        if value is None or str(value).strip() == '':
            return None
        try:
            parsed = datetime.datetime.strptime(str(value).strip(), self.from_fmt)
        except ValueError:
            return None
        return parsed if self.as_datetime else parsed.date()

    def resolve(self, row):
        value = row[self.name]
        converted = self.convert(value)
        if converted is None and not is_blank(value):
            return LostValue(None, INVALID_DATE, value)
        return converted


class EmailExtract(FieldStrategy):
    """Extrai o primeiro e-mail válido do valor ou retorna None."""

    def __init__(self, name: str):
        self.name = name

    @property
    def select_columns(self):
        return [self.name]

    @property
    def insert_col(self):
        return self.name

    def raw_value(self, row):
        value = self.resolve(row)
        return None if isinstance(value, LostValue) else value

    def resolve(self, row):
        raw = row[self.name]
        if is_blank(raw):
            return None
        match = re.search(r'[\w.\-+]+@[\w.\-]+', str(raw))
        if match:
            return match.group(0)
        return LostValue(None, EMAIL_NOT_FOUND, raw)
