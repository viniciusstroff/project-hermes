"""Owner of a source adapter: the process and thread that created it.

A database connection serves one worker. When generation runs tasks in
threads or processes, each task opens its own source connections. An adapter
created outside the task (in ``__init__``, for instance) and read inside it
would share a connection between workers, or use in a child process a socket
of the parent. The adapter records its owner when it is created and refuses
reads from anyone else.
"""

import os
import threading


class AdapterOwnerError(RuntimeError):
    """A source adapter was used by a process or thread other than the one that created it."""


def current_owner() -> tuple:
    return os.getpid(), threading.get_ident()


def claim(adapter):
    """Record the current process and thread as the owner of the adapter."""

    adapter._owner = current_owner()


def check_owner(adapter, name: str = 'de origem'):
    """Refuse a read from a process or thread that does not own the adapter."""

    owner = getattr(adapter, '_owner', None)
    if owner is not None and owner != current_owner():
        raise AdapterOwnerError(
            f'O adapter {name} pertence a outro worker. Dentro de uma tarefa, leia a origem com '
            "self.context.source('<nome>') e não guarde engines criados fora dela."
        )
