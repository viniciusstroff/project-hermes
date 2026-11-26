"""Server versions: read as tuples and checked against the range of an adapter.

Each adapter declares the oldest server it supports for each role
(``supported_versions``) and checks it when it connects. A server outside the
range stops the run before generation, with the adapter, the server version
and the minimum in the message. Behavior that changes between versions stays
in methods of the adapter, not in comparisons spread over the core.
"""

import re

from core.connections import ConnectionConfigError

ROLE_LABELS = {'source': 'origem', 'target': 'destino'}


class UnsupportedVersionError(ConnectionConfigError):
    """The server is outside the range its adapter supports."""


def version_text(version) -> str:
    return '.'.join(str(part) for part in version)


def parse_version(text):
    """``'3.0.10'`` is ``(3, 0, 10)``. Text without a version number gives None."""

    match = re.search(r'\d+(?:\.\d+)*', str(text or ''))
    return tuple(int(part) for part in match.group(0).split('.')) if match else None


def postgres_version(number):
    """Version of a libpq server number: 90624 is (9, 6, 24) and 160004 is (16, 4)."""

    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return None
    major = number // 10000
    if major >= 10:
        return major, number % 10000
    return major, (number // 100) % 100, number % 100


def check_server_version(prefix: str, adapter: str, role: str, found, minimum):
    """Refuse a server older than ``minimum``. An unknown version (None) is not checked."""

    if found is None:
        return
    if tuple(found) < tuple(minimum):
        raise UnsupportedVersionError(
            f'O servidor de {prefix} é {adapter} {version_text(found)}. O adapter {adapter} aceita '
            f'{ROLE_LABELS[role]} a partir da versão {version_text(minimum)}.'
        )
