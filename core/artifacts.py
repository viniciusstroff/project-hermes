import datetime
import json
import os
from dataclasses import dataclass

# Phases in the order of generation and application. The position gives the
# number in the file name: comandos/<NN>_<phase>.<extension>.
PHASES = (
    'setup',
    'prepare_target',
    'copy_compatible_entities',
    'load_transformed_data',
    'finalize_target',
)
COMMANDS_DIR = 'comandos'


@dataclass(frozen=True)
class ArtifactFormat:
    """Format of the artifacts written for a target adapter."""

    name: str
    extension: str


def artifact_name(phase: str, extension: str) -> str:
    """File name of a phase artifact, such as ``03_load_transformed_data.sql``."""

    if phase not in PHASES:
        raise KeyError(f'Fase desconhecida: {phase}')
    return f'{PHASES.index(phase):02d}_{phase}.{extension}'


class ArtifactManager:
    """Centraliza os artefatos gerados por uma execução de migração."""

    def __init__(self, clientdir: str, rootdir: str = None, timestamp: str = None):
        self.clientdir = os.path.abspath(clientdir)
        self.client_name = os.path.basename(self.clientdir)
        self.rootdir = os.path.abspath(rootdir or os.path.dirname(self.clientdir))
        self.timestamp = timestamp or datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.run_dir = os.path.join(self.rootdir, 'runs', self.client_name, self.timestamp)
        self.commands_dir = os.path.join(self.run_dir, COMMANDS_DIR)
        self.logs_dir = os.path.join(self.run_dir, 'logs')
        self.errors_dir = os.path.join(self.run_dir, 'erros')
        self.manifest_path = os.path.join(self.run_dir, 'manifest.json')
        self._artifacts = {}

        for directory in (self.commands_dir, self.logs_dir, self.errors_dir):
            os.makedirs(directory, exist_ok=True)

    def command_path(self, phase: str, extension: str) -> str:
        """Path of the artifact of a phase. The artifact goes to the manifest list."""

        name = artifact_name(phase, extension)
        self._artifacts[phase] = {
            'phase': phase,
            'path': f'{COMMANDS_DIR}/{name}',
            'format': extension,
        }
        return os.path.join(self.commands_dir, name)

    def artifacts(self) -> list:
        """Artifacts handed out in this run, in phase order, with paths relative to the run."""

        return [self._artifacts[phase] for phase in PHASES if phase in self._artifacts]

    def log_path(self, name: str) -> str:
        return os.path.join(self.logs_dir, name)

    def error_path(self, name: str) -> str:
        return os.path.join(self.errors_dir, name)

    def write_manifest(
        self,
        *,
        status: str,
        connections: dict = None,
        debug_limit: int = None,
        error: str = None,
        output: dict = None,
    ):
        manifest = {
            'client': self.client_name,
            'timestamp': self.timestamp,
            'status': status,
            'debug': {
                'enabled': debug_limit is not None,
                'limit': debug_limit,
            },
            'run_dir': self.run_dir,
            'artifacts': self.artifacts(),
            'logs_dir': self.logs_dir,
            'errors_dir': self.errors_dir,
            # Adapter, parameters and server version of each connection, without passwords.
            'sources': (connections or {}).get('sources', {}),
            'target': (connections or {}).get('target'),
            'output': output,
        }

        if error:
            manifest['error'] = error

        with open(self.manifest_path, 'w', encoding='utf-8') as manifest_file:
            json.dump(manifest, manifest_file, indent=2, ensure_ascii=False)
            manifest_file.write('\n')
