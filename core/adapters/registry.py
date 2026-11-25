"""Resolve the adapter named in a connection to the class that implements it.

Built-in names point to the adapters of the core. Any other value in the form
``package.module:Class`` is imported, so an adapter from outside the core needs
no change here. Adapter modules import their database drivers only when they
connect, so a client that only uses PostgreSQL does not need ``firebirdsql``.
"""

import importlib

BUILTIN_ADAPTERS = {
    'postgres': {
        'source': 'core.adapters.postgres:PostgresSourceAdapter',
        'target': 'core.adapters.postgres:PostgresTargetAdapter',
    },
    'firebird': {
        'source': 'core.adapters.firebird:FirebirdSourceAdapter',
    },
}

ROLE_METHODS = {
    'source': ('connect', 'cursor', 'scalar', 'limit_clause', 'close'),
    'target': ('connect', 'serialize_value', 'insert_statement', 'close'),
}

ROLE_NAMES = {'source': 'origem', 'target': 'destino'}


class AdapterResolutionError(ValueError):
    """The adapter name cannot be resolved for the requested role."""


def resolve_adapter(name: str, role: str):
    """Return the adapter class for ``name`` in the ``source`` or ``target`` role."""

    if role not in ROLE_METHODS:
        raise ValueError(f'unknown adapter role: {role!r}')

    if name in BUILTIN_ADAPTERS:
        path = BUILTIN_ADAPTERS[name].get(role)
        if path is None:
            raise AdapterResolutionError(f"O adapter '{name}' não serve como {ROLE_NAMES[role]}.")
    elif ':' in name:
        path = name
    else:
        builtin = ', '.join(sorted(BUILTIN_ADAPTERS))
        raise AdapterResolutionError(
            f"Adapter '{name}' desconhecido. Use um dos embutidos ({builtin}) ou pacote.modulo:Classe."
        )

    adapter_class = _import(path)
    missing = [method for method in ROLE_METHODS[role] if not callable(getattr(adapter_class, method, None))]
    if missing:
        raise AdapterResolutionError(
            f"O adapter '{name}' não implementa o contrato de {ROLE_NAMES[role]}: faltam {', '.join(missing)}."
        )
    return adapter_class


def connect_adapter(config, role: str):
    """Resolve the adapter of a ConnectionConfig and connect it."""

    return resolve_adapter(config.adapter, role).connect(config)


def _import(path: str):
    module_name, _, attribute = path.partition(':')
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise AdapterResolutionError(f"Não foi possível importar o adapter '{path}': {exc}") from exc
