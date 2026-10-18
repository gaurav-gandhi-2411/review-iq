"""Light module (no numpy/torch) so the endpoint can catch the runtime's error without importing the runtime."""

from __future__ import annotations


class ClassifyUnavailableError(RuntimeError):
    """The model cannot be served (artifacts or heavy dependencies missing). `code` is the machine code for the 503."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
