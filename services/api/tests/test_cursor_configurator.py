from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONFIGURATOR = ROOT / "scripts" / "demo" / "configure_cursor_hooks.py"


def load_configurator_module():
    spec = importlib.util.spec_from_file_location("meshagent_cursor_configurator", CONFIGURATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native_hook(path: Path) -> Path:
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = --health ]; then printf '{\"status\":\"ok\"}\\n'; exit 0; fi\n"
        "cat >/dev/null\n"
        "printf '{\"permission\":\"allow\"}\\n'\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def repository(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_atomic_text_works_without_fchmod(tmp_path, monkeypatch):
    module = load_configurator_module()
    monkeypatch.delattr(module.os, "fchmod", raising=False)
    target = tmp_path / "windows-wrapper.cmd"
    module.atomic_text(target, "@echo off\r\n")
    assert target.read_text() == "@echo off\n"


def test_windows_native_events_use_cmd_wrapper_and_fail_open():
    module = load_configurator_module()
    events = module.configured_events(True, platform="nt")
    assert {entry["command"] for entry in events.values()} == {
        r".\.meshagent\run_cursor_hook.cmd"
    }
    assert events["beforeShellExecution"]["timeout"] == 6
    assert events["beforeShellExecution"]["failClosed"] is False


def test_native_mode_preserves_existing_hooks_and_is_idempotent(tmp_path):
    repo = repository(tmp_path / "repo")
    cursor = repo / ".cursor"
    cursor.mkdir()
    original = {
        "version": 1,
        "hooks": {
            "beforeSubmitPrompt": [
                {"type": "command", "command": "./custom-hook", "timeout": 2}
            ],
            "beforeShellExecution": [
                {
                    "type": "command",
                    "command": "./.meshagent/run_cursor_hook.sh",
                    "timeout": 99,
                    "failClosed": True,
                },
                {
                    "type": "command",
                    "command": "./.meshagent/run_cursor_hook.sh",
                    "timeout": 99,
                    "failClosed": True,
                },
            ],
        },
    }
    (cursor / "hooks.json").write_text(json.dumps(original), encoding="utf-8")
    hook = native_hook(tmp_path / "meshagent-hook")
    command = [
        sys.executable,
        os.fspath(CONFIGURATOR),
        os.fspath(repo),
        "--native-hook",
        os.fspath(hook),
        "--local-exclude",
    ]
    first = subprocess.run(command, text=True, capture_output=True, check=True)
    assert "Recorder mode: native IPC" in first.stdout
    document = json.loads((cursor / "hooks.json").read_text())
    assert document["hooks"]["beforeSubmitPrompt"][0]["command"] == "./custom-hook"
    meshagent_entries = [
        entry
        for entries in document["hooks"].values()
        for entry in entries
        if entry.get("command") == "./.meshagent/run_cursor_hook.sh"
    ]
    assert len(meshagent_entries) == 5
    assert document["hooks"]["beforeShellExecution"][-1]["timeout"] == 6
    assert document["hooks"]["beforeShellExecution"][-1]["failClosed"] is False
    assert list(cursor.glob("hooks.json.meshagent-backup-*"))
    second = subprocess.run(command, text=True, capture_output=True, check=True)
    assert "Hook entries: already current" in second.stdout
    wrapper = (repo / ".meshagent" / "run_cursor_hook.sh").read_text()
    assert os.fspath(hook) in wrapper
    assert not (repo / ".meshagent" / "cursor_hook.py").exists()


def test_configurator_refuses_explicit_repository_opt_out(tmp_path):
    repo = repository(tmp_path / "repo")
    (repo / ".meshagent.json").write_text('{"record":false}\n')
    hook = native_hook(tmp_path / "meshagent-hook")
    result = subprocess.run(
        [sys.executable, os.fspath(CONFIGURATOR), os.fspath(repo), "--native-hook", os.fspath(hook)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "refusing to override" in result.stderr
    assert not (repo / ".meshagent").exists()
    assert not (repo / ".cursor").exists()


def test_native_mode_refuses_unreachable_daemon(tmp_path):
    repo = repository(tmp_path / "repo")
    hook = tmp_path / "meshagent-hook"
    hook.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = --health ]; then exit 1; fi\n"
        "cat >/dev/null\nprintf '{\"permission\":\"allow\"}\\n'\n",
        encoding="utf-8",
    )
    hook.chmod(0o700)
    result = subprocess.run(
        [sys.executable, os.fspath(CONFIGURATOR), os.fspath(repo), "--native-hook", os.fspath(hook)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "native recorder is not reachable" in result.stderr
    assert not (repo / ".meshagent").exists()
    assert not (repo / ".cursor").exists()
    assert not (repo / ".meshagent.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation needs an elevated test fixture")
def test_configurator_refuses_symlinked_managed_directory(tmp_path):
    repo = repository(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".cursor").symlink_to(outside, target_is_directory=True)
    hook = native_hook(tmp_path / "meshagent-hook")
    result = subprocess.run(
        [sys.executable, os.fspath(CONFIGURATOR), os.fspath(repo), "--native-hook", os.fspath(hook)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "refusing symlink" in result.stderr
    assert list(outside.iterdir()) == []
    assert not (repo / ".meshagent").exists()
    assert not (repo / ".meshagent.json").exists()
