class SqlFileOutput:
    """Writes generated SQL into one artifact file per migration phase.

    Phase keys are resolved to file paths by ``ArtifactManager.sql_path()``,
    so file names stay centralized in the artifact manager.
    """

    def __init__(self, artifacts, buffer_size: int = 500):
        self.artifacts = artifacts
        self.buffer_size = buffer_size
        self.file = None
        self._write_buffer = []

    def open_phase(self, phase: str):
        self.flush()
        if self.file:
            self.file.close()
        self.file = open(self.artifacts.sql_path(phase), 'w+', encoding='utf-8')

    def write(self, sql: str):
        self._write_buffer.append(sql)
        if len(self._write_buffer) >= self.buffer_size:
            self.flush()

    def comment(self, text: str):
        self.write('\n-----' + len(text) * '-')
        self.write('\n-----' + text)
        self.write('\n-----' + (len(text) * '-') + '\n\n')

    def flush(self):
        if self._write_buffer:
            self.file.write(''.join(self._write_buffer))
            self._write_buffer = []

    def finish(self, success: bool):
        # SQL files are audit artifacts: a failed run keeps its partial files.
        self.flush()

    def describe(self) -> dict:
        return {'type': 'sql-file'}

    def close(self):
        self.flush()
        if self.file:
            self.file.close()
            self.file = None
