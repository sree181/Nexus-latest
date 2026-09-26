#!/usr/bin/env python3
"""Install meshAgent's Cursor recorder into one opted-in Git repository.

The configurator is deliberately local and conservative: it preserves existing
Cursor hook entries, backs up hooks.json before changing it, refuses to override
an explicit repository opt-out, and pins the hook wrapper either to the Stage 1A
native IPC client or a chosen Python interpreter containing meshagent_cli.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

EVENTS: dict[str, dict[str, object]] = {
    "beforeSubmitPrompt": {
        "type": "command",
        "command": "./.meshagent/run_cursor_hook.sh",
        "timeout": 5,
    },
    "afterFileEdit": {
        "type": "command",
        "command": "./.meshagent/run_cursor_hook.sh",
        "timeout": 5,
    },
    "beforeShellExecution": {
        "type": "command",
        "command": "./.meshagent/run_cursor_hook.sh",
        "timeout": 10,
        "failClosed": False,
    },
    "afterShellExecution": {
        "type": "command",
        "command": "./.meshagent/run_cursor_hook.sh",
        "timeout": 5,
    },
    "sessionEnd": {
        "type": "command",
        "command": "./.meshagent/run_cursor_hook.sh",
        "timeout": 10,
    },
}

DEFAULT_OPT_IN = {
    "record": True,
    "exclude": ["secrets/*", "*.pem", "*.key", ".env", ".env.*"],
    "gate": True,
}


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"configure-cursor-hooks: {message}")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}")
    except OSError as exc:
        fail(f"cannot read {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path} must contain a JSON object")
    return value


def require_repository_path(repository: Path, path: Path) -> None:
    try:
        path.relative_to(repository)
    except ValueError:
        fail(f"refusing path outside repository: {path}")
    current = path
    while current != repository:
        if current.is_symlink():
            fail(f"refusing symlink in managed repository path: {current}")
        current = current.parent


def atomic_text(path: Path, content: str, mode: int = 0o600) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(path, json.dumps(value, indent=2) + "\n")


def ensure_runtime(python: Path) -> None:
    if not python.is_file() or not os.access(python, os.X_OK):
        fail(f"Python runtime is not executable: {python}")
    result = subprocess.run(
        [str(python), "-c", "import meshagent_cli"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        fail(
            f"{python} cannot import meshagent_cli; install MeshAgent into that "
            "virtual environment first"
        )


def configured_events(native: bool, platform: str | None = None) -> dict[str, dict[str, object]]:
    configured = {event: dict(entry) for event, entry in EVENTS.items()}
    platform = platform or os.name
    command = (
        r".\.meshagent\run_cursor_hook.cmd"
        if platform == "nt"
        else "./.meshagent/run_cursor_hook.sh"
    )
    for entry in configured.values():
        entry["command"] = command
        if native:
            entry["timeout"] = 3
    if native:
        configured["beforeShellExecution"]["timeout"] = 6
    return configured


def merge_hooks(
    path: Path, wanted_events: dict[str, dict[str, object]] = EVENTS
) -> tuple[bool, Path | None]:
    if path.exists():
        document = load_json(path)
    else:
        document = {"version": 1, "hooks": {}}

    version = document.get("version", 1)
    if version != 1:
        fail(f"{path} uses unsupported Cursor hook version {version!r}")
    document["version"] = 1

    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        fail(f"{path}: hooks must be a JSON object")

    changed = not path.exists()
    for event, wanted in wanted_events.items():
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            fail(f"{path}: hooks.{event} must be an array")

        retained: list[object] = []
        inserted = False
        for entry in entries:
            command = entry.get("command") if isinstance(entry, dict) else None
            is_command = isinstance(entry, dict) and entry.get("type") == "command"
            if is_command and isinstance(command, str):
                if command == wanted["command"]:
                    if not inserted:
                        retained.append(dict(wanted))
                        inserted = True
                    continue
                if (
                    "meshagent_hook.py" in command
                    or ".meshagent/cursor_hook.py" in command
                    or "run_cursor_hook.sh" in command
                    or "run_cursor_hook.cmd" in command
                ):
                    continue
            retained.append(entry)
        if not inserted:
            retained.append(dict(wanted))
        if retained != entries:
            hooks[event] = retained
            changed = True

    backup: Path | None = None
    if changed and path.exists():
        backup = path.with_name(f"{path.name}.meshagent-backup-{int(time.time())}")
        shutil.copy2(path, backup)
    if changed:
        atomic_json(path, document)
    return changed, backup


def append_local_excludes(repository: Path) -> None:
    exclude = repository / ".git" / "info" / "exclude"
    require_repository_path(repository, exclude)
    if not exclude.parent.is_dir():
        fail("--local-exclude requires a normal Git worktree with .git/info")
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = set(existing.splitlines())
    additions = [".meshagent/", ".meshagent.json", ".cursor/hooks.json"]
    with exclude.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        for item in additions:
            if item not in lines:
                handle.write(f"{item}\n")


def require_native_daemon(native_hook: Path) -> None:
    health = subprocess.run(
        [str(native_hook), "--health"],
        text=True,
        capture_output=True,
        check=False,
        timeout=3,
    )
    try:
        health_body = json.loads(health.stdout)
    except json.JSONDecodeError:
        health_body = {}
    if health.returncode or health_body.get("status") != "ok":
        fail(
            "native recorder is not reachable; start meshagent-recorder "
            "before configuring Cursor"
        )


def smoke(repository: Path, wrapper: Path) -> None:
    payload = {
        "hook_event_name": "beforeShellExecution",
        "conversation_id": "meshagent-hook-preflight",
        "cwd": str(repository),
        "command": "echo hook-ready",
    }
    command = [str(wrapper)]
    if os.name == "nt":
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", str(wrapper)]
    result = subprocess.run(
        command,
        cwd=repository,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    if result.returncode:
        fail(f"hook smoke failed: {result.stderr.strip() or 'unknown error'}")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(f"hook smoke returned invalid JSON: {result.stdout!r}")
    if response.get("permission") != "allow":
        fail(f"harmless hook smoke was not allowed: {response!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely merge MeshAgent recording hooks into one Cursor repository"
    )
    parser.add_argument(
        "repository",
        nargs="?",
        default=".",
        help="Git repository opened by Cursor (default: current directory)",
    )
    parser.add_argument(
        "--python",
        default=os.environ.get(
            "MESHAGENT_PYTHON", str(Path.home() / ".venvs/meshagent/bin/python")
        ),
        help="Python interpreter containing meshagent_cli",
    )
    parser.add_argument(
        "--native-hook",
        help=(
            "absolute path to the installed Stage 1A meshagent-hook binary; "
            "uses local IPC instead of copying the Python hook"
        ),
    )
    parser.add_argument(
        "--local-exclude",
        action="store_true",
        help="exclude generated hook/config paths through .git/info/exclude",
    )
    args = parser.parse_args()

    repository = Path(args.repository).expanduser().resolve()
    if not repository.is_dir():
        fail(f"repository does not exist: {repository}")
    if not (repository / ".git").exists():
        fail(f"not a Git worktree: {repository}")

    release_root = Path(__file__).resolve().parents[2]
    source_hook = release_root / "adapters/cursor/meshagent_hook.py"
    native_hook = (
        Path(os.path.abspath(Path(args.native_hook).expanduser()))
        if args.native_hook
        else None
    )
    if native_hook is not None:
        if not native_hook.is_file() or not os.access(native_hook, os.X_OK):
            fail(f"native hook is not executable: {native_hook}")
        require_native_daemon(native_hook)
        python = None
    else:
        if not source_hook.is_file():
            fail(f"release hook is missing: {source_hook}")
        # Keep the venv path itself: resolving its `python` symlink would bypass
        # the environment and lose the installed meshagent_cli package.
        python = Path(os.path.abspath(Path(args.python).expanduser()))
        ensure_runtime(python)

    local_dir = repository / ".meshagent"
    cursor_dir = repository / ".cursor"
    opt_in = repository / ".meshagent.json"
    for managed_path in (local_dir, cursor_dir, opt_in, cursor_dir / "hooks.json"):
        require_repository_path(repository, managed_path)
    if opt_in.exists():
        current = load_json(opt_in)
        if current.get("record") is not True:
            fail(
                f"{opt_in} does not explicitly set record=true; refusing to "
                "override the repository's privacy choice"
            )

    local_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    cursor_dir.mkdir(parents=True, exist_ok=True)

    wrapper = local_dir / ("run_cursor_hook.cmd" if os.name == "nt" else "run_cursor_hook.sh")
    require_repository_path(repository, wrapper)
    if native_hook is not None:
        if os.name == "nt":
            atomic_text(wrapper, f'@echo off\r\n"{native_hook}"\r\n', 0o700)
        else:
            atomic_text(
                wrapper,
                "#!/bin/sh\n"
                "set -eu\n"
                f"exec {shlex.quote(str(native_hook))}\n",
                0o700,
            )
    else:
        target_hook = local_dir / "cursor_hook.py"
        require_repository_path(repository, target_hook)
        atomic_text(target_hook, source_hook.read_text(encoding="utf-8"))
        if os.name == "nt":
            atomic_text(wrapper, f'@echo off\r\n"{python}" "%~dp0cursor_hook.py"\r\n', 0o700)
        else:
            atomic_text(
                wrapper,
                "#!/bin/sh\n"
                "set -eu\n"
                f"PYTHON={shlex.quote(str(python))}\n"
                "exec \"$PYTHON\" \"$(dirname \"$0\")/cursor_hook.py\"\n",
                0o700,
            )

    if not opt_in.exists():
        atomic_json(opt_in, DEFAULT_OPT_IN)

    changed, backup = merge_hooks(
        cursor_dir / "hooks.json", configured_events(native_hook is not None)
    )
    if args.local_exclude:
        append_local_excludes(repository)

    smoke(repository, wrapper)

    print(f"Repository: {repository}")
    print(
        f"Recorder mode: native IPC ({native_hook})"
        if native_hook
        else f"Recorder mode: Python compatibility ({python})"
    )
    print(f"Repository opt-in: {opt_in}")
    print(f"Cursor hooks: {cursor_dir / 'hooks.json'}")
    print("Hook entries: " + ("updated" if changed else "already current"))
    if backup:
        print(f"Previous hooks backup: {backup}")
    if native_hook:
        print("Native daemon: connected")
    print("Smoke: harmless beforeShellExecution returned allow")
    print("Next: fully quit Cursor, reopen this repository root, and start a new conversation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
