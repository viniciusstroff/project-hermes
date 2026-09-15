from importlib import import_module

from core.adapters.source_adapter import SourceAdapter, SourceCursor
from core.adapters.target_adapter import TargetAdapter

_LAZY_EXPORTS = {
    'FirebirdCursorAdapter': ('core.adapters.firebird', 'FirebirdCursorAdapter'),
    'FirebirdSourceAdapter': ('core.adapters.firebird', 'FirebirdSourceAdapter'),
    'PostgresSourceAdapter': ('core.adapters.postgres', 'PostgresSourceAdapter'),
    'PostgresTargetAdapter': ('core.adapters.postgres', 'PostgresTargetAdapter'),
}

__all__ = [
    'FirebirdCursorAdapter',
    'FirebirdSourceAdapter',
    'PostgresSourceAdapter',
    'PostgresTargetAdapter',
    'SourceAdapter',
    'SourceCursor',
    'TargetAdapter',
]


def __getattr__(name):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

    module_name, attr_name = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
