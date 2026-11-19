"""Process locks must exclude contenders and work without fcntl on Windows."""

import errno
import importlib.util
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from simtradedata.utils import process_lock


def test_lock_excludes_another_process_and_releases_after_error(tmp_path):
    path = tmp_path / "download.lock"
    code = (
        "from simtradedata.utils.process_lock import ProcessLock; "
        f"lock = ProcessLock({str(path)!r}); lock.__enter__(); lock.__exit__(None, None, None)"
    )
    with pytest.raises(ValueError):
        with process_lock.ProcessLock(str(path)):
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            assert result.returncode == 1
            assert "Another download process" in result.stdout
            raise ValueError("download failed")
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


def test_lock_file_is_not_truncated_by_contender_or_removed_on_release(tmp_path):
    path = tmp_path / "download.lock"
    with process_lock.ProcessLock(str(path)):
        contender = process_lock.ProcessLock(str(path))
        with pytest.raises(SystemExit):
            contender.__enter__()
        assert path.read_text() == str(os.getpid())
        assert contender.lock_fd.closed
    assert path.exists()


def test_windows_lock_backend_without_fcntl(monkeypatch, tmp_path):
    held = set()

    def locking(fd, mode, size):
        assert size == 1
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
        if mode == 1:
            if held:
                raise OSError(errno.EACCES, "lock held")
            held.add(fd)
        else:
            held.remove(fd)

    backend = SimpleNamespace(locking=locking, LK_NBLCK=1, LK_UNLCK=2)
    spec = importlib.util.spec_from_file_location("windows_process_lock", process_lock.__file__)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        patch.setitem(sys.modules, "fcntl", None)
        patch.setitem(sys.modules, "msvcrt", backend)
        spec.loader.exec_module(module)

    path = tmp_path / "windows.lock"
    with module.ProcessLock(str(path)):
        contender = module.ProcessLock(str(path))
        with pytest.raises(SystemExit):
            contender.__enter__()
        assert contender.lock_fd.closed
    assert not held
    with module.ProcessLock(str(path)):
        assert held
    assert not held
