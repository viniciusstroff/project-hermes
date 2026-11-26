import shutil


class FileOutput:
    """Writes the commands of each operation into one artifact file per phase.

    The target adapter renders each operation (``render(operation)``) and gives
    the artifact format, whose extension names the files:
    ``comandos/<NN>_<phase>.<extension>``. Paths come from the artifact manager,
    which also lists them in the manifest.
    """

    def __init__(self, artifacts, target, buffer_size: int = 500):
        self.artifacts = artifacts
        self.target = target
        self.format = target.artifact_format
        self.buffer_size = buffer_size
        self.file = None
        self._write_buffer = []

    def open_phase(self, phase: str):
        self.flush()
        if self.file:
            self.file.close()
        # newline='': a carriage return inside a value is written as it is.
        self.file = open(
            self.artifacts.command_path(phase, self.format.extension), 'w+', encoding='utf-8', newline='',
        )

    def write(self, operation):
        # Rendering may fail (a value the target cannot store): the error goes
        # up to the migration that emitted the operation.
        self._write_buffer.extend(self.target.render(operation))
        if len(self._write_buffer) >= self.buffer_size:
            self.flush()

    def include_file(self, path: str):
        self.flush()
        # newline='': copied byte for byte, \r\n and lone \r included.
        with open(path, encoding='utf-8', newline='') as source:
            shutil.copyfileobj(source, self.file)

    def flush(self):
        if self._write_buffer:
            self.file.write(''.join(self._write_buffer))
            self._write_buffer = []

    def finish(self, success: bool):
        # Artifacts are kept for audit: a failed run keeps its partial files.
        # Flushing the file too lets the generation report read the final sizes.
        self.flush()
        if self.file:
            self.file.flush()

    def describe(self) -> dict:
        return {'type': 'file', 'format': self.format.name}

    def close(self):
        self.flush()
        if self.file:
            self.file.close()
            self.file = None
