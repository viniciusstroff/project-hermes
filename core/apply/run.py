"""Read a generated run directory and check that it can be applied."""

import json
import os
from dataclasses import dataclass

from core.artifacts import COMMANDS_DIR, PHASES, artifact_name

# Phase order comes from the artifact manager, the same order of generation.
# The applier only handles SQL artifacts for now.
PHASE_FILES = tuple(artifact_name(phase, 'sql') for phase in PHASES)
DUMP_FILE = artifact_name('dump_compatible_tables', 'sql')


class IncompleteRunError(Exception):
    """Raised when a run directory cannot be applied."""


@dataclass(frozen=True)
class Artifact:
    name: str
    path: str


@dataclass(frozen=True)
class GeneratedRun:
    """SQL artifacts of a successful generation, in phase order.

    Paths are resolved from ``run_dir`` and not from the manifest, because
    the run directory may have been copied to another machine.
    """

    run_dir: str
    debug: bool
    artifacts: tuple

    @classmethod
    def load(cls, run_dir: str) -> 'GeneratedRun':
        run_dir = os.path.abspath(run_dir)
        manifest = _read_manifest(run_dir)

        status = manifest.get('status')
        if status != 'success':
            raise IncompleteRunError(
                f'{run_dir}: a geração terminou com status {status!r}. '
                'Só execuções com status success podem ser aplicadas.'
            )

        debug = bool((manifest.get('debug') or {}).get('enabled'))
        artifacts = []
        missing = []
        for name in PHASE_FILES:
            path = os.path.join(run_dir, COMMANDS_DIR, name)
            if os.path.isfile(path):
                artifacts.append(Artifact(name, path))
            elif not (debug and name == DUMP_FILE):
                # A debug run does not call pg_dump, so it has no dump file.
                missing.append(name)

        if missing:
            raise IncompleteRunError(f'{run_dir}: artefatos ausentes em {COMMANDS_DIR}/: {", ".join(missing)}')

        return cls(run_dir, debug, tuple(artifacts))


def _read_manifest(run_dir: str) -> dict:
    path = os.path.join(run_dir, 'manifest.json')
    if not os.path.isfile(path):
        raise IncompleteRunError(f'{run_dir}: manifest.json não encontrado')
    with open(path, encoding='utf-8') as manifest_file:
        return json.load(manifest_file)
