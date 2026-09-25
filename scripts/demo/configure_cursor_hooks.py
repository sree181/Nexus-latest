#!/usr/bin/env python3
"""Install MeshAgent's Cursor recorder into one opted-in Git repository.

The configurator is deliberately local and conservative: it preserves existing
Cursor hook entries, backs up hooks.json before changing it, refuses to override
an explicit repository opt-out, and pins the hook wrapper to a chosen Python
interpreter containing meshagent_cli.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
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


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


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


def merge_hooks(path: Path) -> tuple[bool, Path | None]:
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
    for event, wanted in EVENTS.items():
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            fail(f"{path}: hooks.{event} must be an array")

        retained = [
            entry
            for entry in entries
            if not (
                isinstance(entry, dict)
                and entry.get("type") == "command"
                and isinstance(entry.get("command"), str)
                and entry.get("command") != wanted["command"]
                and (
                    "meshagent_hook.py" in entry["command"]
                    or ".meshagent/cursor_hook.py" in entry["command"]
                    or "run_cursor_hook.sh" in entry["command"]
                )
            )
        ]
        if retained != entries:
            hooks[event] = entries = retained
            changed = True

        if any(
            isinstance(entry, dict)
            and entry.get("type") == "command"
            and entry.get("command") == wanted["command"]
            for entry in entries
        ):
            continue
        entries.append(dict(wanted))
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


def smoke(repository: Path, wrapper: Path) -> None:
    payload = {
        "hook_event_name": "beforeShellExecution",
        "conversation_id": "meshagent-hook-preflight",
        "cwd": str(repository),
        "command": "echo hook-ready",
    }
    result = subprocess.run(
        [str(wrapper)],
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
    if not source_hook.is_file():
        fail(f"release hook is missing: {source_hook}")

    # Keep the venv path itself: resolving its `python` symlink would bypass the
    # environment and lose the installed meshagent_cli package.
    python = Path(os.path.abspath(Path(args.python).expanduser()))
    ensure_runtime(python)

    local_dir = repository / ".meshagent"
    cursor_dir = repository / ".cursor"
    local_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    cursor_dir.mkdir(parents=True, exist_ok=True)

    target_hook = local_dir / "cursor_hook.py"
    shutil.copy2(source_hook, target_hook)

    wrapper = local_dir / "run_cursor_hook.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f"PYTHON={shlex.quote(str(python))}\n"
        "exec \"$PYTHON\" \"$(dirname \"$0\")/cursor_hook.py\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    opt_in = repository / ".meshagent.json"
    if opt_in.exists():
        current = load_json(opt_in)
        if current.get("record") is not True:
            fail(
                f"{opt_in} does not explicitly set record=true; refusing to "
                "override the repository's privacy choice"
            )
    else:
        atomic_json(opt_in, DEFAULT_OPT_IN)

    changed, backup = merge_hooks(cursor_dir / "hooks.json")
    if args.local_exclude:
        append_local_excludes(repository)

    smoke(repository, wrapper)

    print(f"Repository: {repository}")
    print(f"Python: {python}")
    print(f"Repository opt-in: {opt_in}")
    print(f"Cursor hooks: {cursor_dir / 'hooks.json'}")
    print("Hook entries: " + ("updated" if changed else "already current"))
    if backup:
        print(f"Previous hooks backup: {backup}")
    print("Smoke: harmless beforeShellExecution returned allow")
    print("Next: fully quit Cursor, reopen this repository root, and start a new conversation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
