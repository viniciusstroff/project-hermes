class MirrorOutput:
    """Delivers the same operations to several outputs, in the given order.

    Delivery calls (``open_phase``, ``write``, ``flush``) stop at
    the first error, which fails the run. ``finish`` and ``close`` reach every
    output even when one of them fails, so a failing database output does not
    keep the file output from finishing, and the first error is raised at the
    end.
    """

    def __init__(self, *outputs):
        if not outputs:
            raise ValueError('MirrorOutput precisa de pelo menos um output')
        self.outputs = outputs

    def open_phase(self, phase: str):
        for output in self.outputs:
            output.open_phase(phase)

    def write(self, operation):
        for output in self.outputs:
            output.write(operation)

    def flush(self):
        for output in self.outputs:
            output.flush()

    def finish(self, success: bool):
        self._call_all(lambda output: output.finish(success))

    def describe(self) -> dict:
        return {
            'type': 'mirror',
            'outputs': [output.describe() for output in self.outputs],
        }

    def close(self):
        self._call_all(lambda output: output.close())

    def _call_all(self, call):
        first_error = None
        for output in self.outputs:
            try:
                call(output)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
