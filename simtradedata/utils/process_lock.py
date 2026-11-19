"""Process-level mutual exclusion lock to prevent concurrent pipeline runs."""

import errno
import os
import sys
from pathlib import Path

_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:
    import msvcrt
else:
    import fcntl


class ProcessLock:
    """Prevent concurrent runs with a platform-native, nonblocking file lock.

    Keep the lock file after release so contenders always lock the same file.
    """

    def __init__(self, lock_file: str):
        self.lock_file = Path(lock_file)
        self.lock_fd = None

    def __enter__(self):
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock_fd = open(self.lock_file, "a+")
        self.lock_fd.seek(0)

        try:
            if _IS_WINDOWS:
                msvcrt.locking(self.lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self.lock_fd.close()
            if e.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            print("\nError: Another download process is running")
            print(f"Lock file: {self.lock_file}")
            sys.exit(1)

        try:
            self.lock_fd.truncate(0)
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
        except Exception:
            self.lock_fd.close()
            raise

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_fd and not self.lock_fd.closed:
            try:
                if _IS_WINDOWS:
                    self.lock_fd.seek(0)
                    msvcrt.locking(self.lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            finally:
                self.lock_fd.close()
