class PipelineError(RuntimeError):
    """A safe error code/message suitable for a persisted job or API response."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class IdempotencyConflict(Exception):
    pass


class QueueFull(Exception):
    pass
