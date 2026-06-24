"""Tests for the cross-platform shims in ``_platform``.

POSIX-focused (CI runs ubuntu + macos); Windows branches are marked
``pragma: no cover`` in the source.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from fred_macro_mcp import _platform

# ---------------------------------------------------------------------------
# state_root
# ---------------------------------------------------------------------------


def test_state_root_honors_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert _platform.state_root() == tmp_path


def test_state_root_posix_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(_platform, "IS_WINDOWS", False)
    root = _platform.state_root()
    assert root == Path.home() / ".local" / "state"


# ---------------------------------------------------------------------------
# secure_chmod / is_secure_perms / file_mode
# ---------------------------------------------------------------------------


@pytest.mark.posix_only
def test_secure_chmod_sets_perms(tmp_path: Path) -> None:
    target = tmp_path / "d"
    target.mkdir()
    _platform.secure_chmod(target, 0o700)
    assert stat.S_IMODE(target.lstat().st_mode) == 0o700
    assert _platform.is_secure_perms(target, 0o700)
    assert _platform.file_mode(target) == 0o700


@pytest.mark.posix_only
def test_secure_chmod_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("nope")

    monkeypatch.setattr(_platform.os, "chmod", boom)
    with caplog.at_level(logging.WARNING):
        _platform.secure_chmod(tmp_path, 0o700)  # must not raise
    assert "secure_chmod failed" in caplog.text


def test_is_secure_perms_missing(tmp_path: Path) -> None:
    assert _platform.is_secure_perms(tmp_path / "absent", 0o700) is False


# ---------------------------------------------------------------------------
# restrictive_umask
# ---------------------------------------------------------------------------


@pytest.mark.posix_only
def test_restrictive_umask_restores() -> None:
    before = os.umask(0o022)
    os.umask(before)
    with _platform.restrictive_umask():
        pass
    after = os.umask(0o022)
    os.umask(after)
    assert before == after


# ---------------------------------------------------------------------------
# notify_desktop — best-effort, never raises
# ---------------------------------------------------------------------------


def test_notify_desktop_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_platform, "IS_MACOS", False)
    monkeypatch.setattr(_platform, "IS_LINUX", False)
    monkeypatch.setattr(_platform, "IS_WINDOWS", False)
    _platform.notify_desktop("t", "m")  # no-op, no raise


def test_notify_desktop_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_platform, "IS_MACOS", True)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(_platform, "_notify_macos", boom)
    _platform.notify_desktop("t", "m")  # swallowed


def test_notify_macos_no_osascript(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _x: None)
    _platform._notify_macos("t", "m")  # returns early, no raise


def test_notify_linux_no_notify_send(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _x: None)
    _platform._notify_linux("t", "m")  # returns early


def test_notify_macos_invokes_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda _x: "/usr/bin/osascript")
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(list(a[0])))
    _platform._notify_macos("title", "msg")
    assert calls and "osascript" in calls[0][0]


def test_notify_linux_invokes_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda _x: "/usr/bin/notify-send")
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(list(a[0])))
    _platform._notify_linux("title", "msg")
    assert calls and "notify-send" in calls[0][0]


def test_notify_desktop_linux_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_platform, "IS_MACOS", False)
    monkeypatch.setattr(_platform, "IS_LINUX", True)
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(_platform, "_notify_linux", lambda t, m: seen.append((t, m)))
    _platform.notify_desktop("t", "m")
    assert seen == [("t", "m")]


def test_notify_desktop_macos_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_platform, "IS_MACOS", True)
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(_platform, "_notify_macos", lambda t, m: seen.append((t, m)))
    _platform.notify_desktop("t", "m")
    assert seen == [("t", "m")]
