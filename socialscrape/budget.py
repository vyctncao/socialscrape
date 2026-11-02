"""A request's native work budget, also copied into blocking extractor workers."""
from contextvars import ContextVar
from time import monotonic

deadline: ContextVar[float | None] = ContextVar("native_deadline", default=None)


def remaining(default: float) -> float:
    end = deadline.get()
    return default if end is None else max(0.0, min(default, end - monotonic()))
