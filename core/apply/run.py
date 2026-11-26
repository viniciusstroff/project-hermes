"""Read a generated run directory and check that it can be applied."""

import json
import os
from dataclasses import dataclass

from core.artifacts import PHASES


class IncompleteRunError(Exception):
    """Raised when a run directory cannot be applied."""


@dataclass(frozen=True)
class Artifact:
    phase: str
    name: str
    path: str
    format: str


@dataclass(frozen=True)
class GeneratedRun:
    """Artifacts of a successful generation, in phase order, and the target adapter.

    The list comes from the manifest, with paths relative to the run
    directory, so the directory may have been copied to another machine.
    """

    run_dir: str
    debug: bool
    target_adapter: str
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

        target_adapter = (manifest.get('target') or {}).get('adapter')
        if not target_adapter:
            raise IncompleteRunError(f'{run_dir}: o manifesto não registra o adapter de destino.')

        listed = manifest.get('artifacts') or []
        if not listed:
            raise IncompleteRunError(f'{run_dir}: o manifesto não lista artefatos.')

        artifacts = []
        missing = []
        for entry in sorted(listed, key=lambda entry: _phase_position(run_dir, entry)):
            path = _artifact_path(run_dir, entry.get('path', ''))
            if os.path.isfile(path):
                artifacts.append(Artifact(entry['phase'], os.path.basename(path), path, entry.get('format')))
            else:
                missing.append(entry.get('path'))
        if missing:
            raise IncompleteRunError(f'{run_dir}: artefatos do manifesto ausentes: {", ".join(missing)}')

        debug = bool((manifest.get('debug') or {}).get('enabled'))
        return cls(run_dir, debug, target_adapter, tuple(artifacts))


def _phase_position(run_dir: str, entry: dict) -> int:
    phase = entry.get('phase')
    if phase not in PHASES:
        raise IncompleteRunError(f'{run_dir}: o manifesto lista a fase desconhecida {phase!r}.')
    return PHASES.index(phase)


def _artifact_path(run_dir: str, relative: str) -> str:
    # Artifacts live inside the run directory; anything else is not ours.
    path = os.path.normpath(os.path.join(run_dir, relative))
    if os.path.isabs(relative) or not path.startswith(run_dir + os.sep):
        raise IncompleteRunError(f'{run_dir}: caminho de artefato fora da execução: {relative!r}')
    return path


def _read_manifest(run_dir: str) -> dict:
    path = os.path.join(run_dir, 'manifest.json')
    if not os.path.isfile(path):
        raise IncompleteRunError(f'{run_dir}: manifest.json não encontrado')
    with open(path, encoding='utf-8') as manifest_file:
        return json.load(manifest_file)
