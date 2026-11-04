"""A workspace-scoped lock for collection and state replacement."""

import fcntl
import os
import stat
from contextlib import contextmanager


@contextmanager
def exclusive_work(store):
    """Serialize public refresh and replacement while leaving reads and tracking available."""
    store.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = store.path.with_name(store.path.name + ".work-lock")
    descriptor = os.open(
        path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            raise ValueError("Workspace operation lock must be owner-only")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "Another collection or recovery operation is running"
            ) from exc
        yield
    finally:
        os.close(descriptor)
