"""Named connections declared in the client ``.env``.

Each source is a group of keys with the same prefix, ``SOURCE_<NAME>_``, with
a ``SOURCE_<NAME>_ADAPTER`` key. The target uses ``TARGET_ADAPTER`` and the
other ``TARGET_`` keys. The remaining suffixes of a group, in lower case, are
the adapter parameters::

    SOURCE_V1_ADAPTER=postgres
    SOURCE_V1_HOST=acmeedu-v1      -> source 'v1', parameter 'host'
    TARGET_ADAPTER=postgres
    TARGET_HOST=acmeedu-v2         -> target, parameter 'host'
"""

import re
from dataclasses import dataclass, field
from typing import Mapping

SOURCE_ADAPTER_KEY = re.compile(r'SOURCE_([A-Z0-9_]+)_ADAPTER')
# Parameters that never go to the manifest or to the log.
SECRET_PARAMETERS = {'pass', 'password', 'uri'}


class ConnectionConfigError(ValueError):
    """The connection keys of the client .env are missing or inconsistent."""


@dataclass(frozen=True)
class ConnectionConfig:
    """One named connection: the adapter name and its parameters."""

    prefix: str
    name: str
    adapter: str
    params: Mapping[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str = None):
        value = self.params.get(key)
        return value if value not in (None, '') else default

    def require(self, *keys: str):
        """Fail with the full .env key names of the required parameters that are empty."""

        missing = [f'{self.prefix}_{key.upper()}' for key in keys if self.get(key) is None]
        if missing:
            raise ConnectionConfigError(
                f'Conexão {self.prefix} ({self.adapter}) sem os parâmetros obrigatórios: {", ".join(missing)}'
            )

    def describe(self) -> dict:
        """Adapter and parameters without secrets, for the manifest and the log."""

        description = {'adapter': self.adapter}
        for key in sorted(self.params):
            if key not in SECRET_PARAMETERS:
                description[key] = self.params[key]
        return description


def parse_connections(config: Mapping[str, str]):
    """Return ``(sources, target)``: a dict of source configs by name and the target config."""

    values = {key: ('' if value is None else str(value).strip()) for key, value in config.items()}

    source_names = sorted(
        (match.group(1) for key in values if (match := SOURCE_ADAPTER_KEY.fullmatch(key))),
        key=len,
        reverse=True,
    )
    if not source_names:
        raise ConnectionConfigError('Nenhuma origem declarada. Declare pelo menos uma com SOURCE_<NOME>_ADAPTER.')

    source_params = {name: {} for name in source_names}
    for key, value in values.items():
        if not key.startswith('SOURCE_') or SOURCE_ADAPTER_KEY.fullmatch(key):
            continue
        # Longest declared name first, so SOURCE_V1_LEGACY_HOST goes to V1_LEGACY.
        owner = next((name for name in source_names if key.startswith(f'SOURCE_{name}_')), None)
        if owner is None:
            raise ConnectionConfigError(
                f'{key} não pertence a nenhuma origem declarada. Declare SOURCE_<NOME>_ADAPTER para ela.'
            )
        source_params[owner][key[len(f'SOURCE_{owner}_'):].lower()] = value

    sources = {}
    for name in sorted(source_names):
        adapter = values[f'SOURCE_{name}_ADAPTER']
        if not adapter:
            raise ConnectionConfigError(f'SOURCE_{name}_ADAPTER está vazio.')
        sources[name.lower()] = ConnectionConfig(f'SOURCE_{name}', name.lower(), adapter, source_params[name])

    target_adapter = values.get('TARGET_ADAPTER', '')
    if not target_adapter:
        raise ConnectionConfigError('TARGET_ADAPTER não definido. Declare o destino com TARGET_ADAPTER e TARGET_*.')
    target_params = {
        key[len('TARGET_'):].lower(): value
        for key, value in values.items()
        if key.startswith('TARGET_') and key != 'TARGET_ADAPTER'
    }
    target = ConnectionConfig('TARGET', 'target', target_adapter, target_params)
    return sources, target
