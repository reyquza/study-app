"""Whether the configured grading model can still be called.

Preview models can be shut down with two weeks' notice. When the model API answers that the
model does not exist, the app stops accepting answers and tells visitors why, instead of
failing every submission with a generic error. State is per process: a restart forgets it,
and the first failed grading or the periodic check (app.worker) sets it again.
"""

import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_unavailable_since: datetime | None = None


def mark_unavailable() -> None:
    global _unavailable_since
    with _lock:
        if _unavailable_since is None:
            _unavailable_since = datetime.now(timezone.utc)


def mark_available() -> None:
    global _unavailable_since
    with _lock:
        _unavailable_since = None


def is_unavailable() -> bool:
    return _unavailable_since is not None
