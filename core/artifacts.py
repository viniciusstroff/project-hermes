import datetime
import json
import os


class ArtifactManager:
    """Centraliza os artefatos gerados por uma execução de migração."""

    SQL_FILES = {
        'setup': '00_setup.sql',
        'prepare_target': '01_prepare_target.sql',
        'dump_compatible_tables': '02_dump_compatible_tables.sql',
        'load_transformed_data': '03_load_transformed_data.sql',
        'finalize_target': '04_finalize_target.sql',
    }

    def __init__(self, clientdir: str, rootdir: str = None, timestamp: str = None):
        self.clientdir = os.path.abspath(clientdir)
        self.client_name = os.path.basename(self.clientdir)
        self.rootdir = os.path.abspath(rootdir or os.path.dirname(self.clientdir))
        self.timestamp = timestamp or datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        self.run_dir = os.path.join(self.rootdir, 'runs', self.client_name, self.timestamp)
        self.sql_dir = os.path.join(self.run_dir, 'sql')
        self.logs_dir = os.path.join(self.run_dir, 'logs')
        self.errors_dir = os.path.join(self.run_dir, 'erros')
        self.manifest_path = os.path.join(self.run_dir, 'manifest.json')

        for directory in (self.sql_dir, self.logs_dir, self.errors_dir):
            os.makedirs(directory, exist_ok=True)

    def sql_path(self, key: str) -> str:
        return os.path.join(self.sql_dir, self.SQL_FILES[key])

    def log_path(self, name: str) -> str:
        return os.path.join(self.logs_dir, name)

    def error_path(self, name: str) -> str:
        return os.path.join(self.errors_dir, name)

    def sql_paths(self) -> dict:
        return {key: self.sql_path(key) for key in self.SQL_FILES}

    def write_manifest(self, *, status: str, config: dict = None, debug_limit: int = None, error: str = None):
        manifest = {
            'client': self.client_name,
            'timestamp': self.timestamp,
            'status': status,
            'debug': {
                'enabled': debug_limit is not None,
                'limit': debug_limit,
            },
            'run_dir': self.run_dir,
            'sql_files': self.sql_paths(),
            'logs_dir': self.logs_dir,
            'errors_dir': self.errors_dir,
            'databases': self._database_config(config or {}),
        }

        if error:
            manifest['error'] = error

        with open(self.manifest_path, 'w', encoding='utf-8') as manifest_file:
            json.dump(manifest, manifest_file, indent=2, ensure_ascii=False)
            manifest_file.write('\n')

    def _database_config(self, config: dict) -> dict:
        return {
            'pg_v1': self._safe_database_entry(config, 'PG_V1', ('HOST', 'PORT', 'NAME', 'USER')),
            'pg_v2': self._safe_database_entry(config, 'PG_V2', ('HOST', 'PORT', 'NAME', 'USER')),
            'fb_v1': self._safe_database_entry(config, 'FB_V1', ('HOST', 'PORT', 'NAME', 'USER', 'CHARSET')),
        }

    def _safe_database_entry(self, config: dict, prefix: str, suffixes: tuple) -> dict:
        entry = {}
        for suffix in suffixes:
            key = f'{prefix}_{suffix}'
            if key in config:
                entry[suffix.lower()] = config[key]
        return entry
