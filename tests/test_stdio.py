"""Tests for the stdio-hardening + dotenv bootstrap in ``server``.

These re-invoke the module-level helpers under monkeypatched failure
conditions to exercise the defensive branches (mkdir failure, file-handler
failure, log_dir None) without disturbing the already-imported module.
"""

from __future__ import annotations

import builtins
import io

import pytest

from fred_macro_mcp import server as server_mod


def test_safe_print_defaults_to_stderr() -> None:
    # _harden_stdio installed a wrapper that defaults file= to stderr.
    server_mod._harden_stdio()
    buf = io.StringIO()
    builtins.print("hello", file=buf)  # explicit file honored
    assert "hello" in buf.getvalue()
    # No-file call must not raise (routes to stderr).
    builtins.print("to stderr")


def test_harden_stdio_mkdir_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from fred_macro_mcp import _platform

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("mkdir denied")

    monkeypatch.setattr(_platform.Path, "mkdir", boom)
    # Should swallow the OSError and fall back to log_dir=None.
    server_mod._harden_stdio()


def test_harden_stdio_file_handler_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("cannot open log")

    monkeypatch.setattr(server_mod, "RotatingFileHandler", boom)
    # Should swallow the OSError and keep just the stderr handler.
    server_mod._harden_stdio()


def test_bootstrap_dotenv_runs() -> None:
    # Idempotent + import-safe; just exercise the call.
    server_mod._bootstrap_dotenv()
