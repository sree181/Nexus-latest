"""Durable local state shared by the MeshAgent CLI and editor adapters.

This module intentionally uses only the Python standard library.  Hooks import it
when the CLI is installed; source-checkout hooks add ``cli/`` to ``sys.path`` as a
compatibility fallback.  Tokens are never returned by diagnostic helpers.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

HOME_ENV = "MESHAGENT_HOOK_HOME"
API_ENV = "MESHAGENT_API"
READ_TOKEN_ENV = "MESHAGENT_READ_TOKEN"
DEVICE_TOKEN_ENV = "MESHAGENT_TOKEN"
DEFAULT_API = "http://localhost:8000"
CONFIG_FILE = "config.json"
CREDENTIALS_FILE = "credentials.json"
STATE_DIR = "state"
QUEUE_DIR = "queues"

# Retention is deliberately bounded.  A laptop kept offline must not accumulate
# unbounded source snapshots under $HOME.  The newest batches are retained,
# because they represent the current working tree.
DEFAULT_MAX_QUEUE_BATCHES = 200
DEFAULT_MAX_QUEUE_BYTES = 5 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ConfigurationError(ValueError):
    """A local endpoint setting is malformed or unsafe to use."""


def _limit(name: str, default: int, ceiling: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return min(max(1, value), ceiling)


def max_queue_batches() -> int:
    return _limit("MESHAGENT_QUEUE_MAX_BATCHES", DEFAULT_MAX_QUEUE_BATCHES, 10000)


def max_queue_bytes() -> int:
    return _limit("MESHAGENT_QUEUE_MAX_BYTES", DEFAULT_MAX_QUEUE_BYTES, 100 * 1024 * 1024)


def home() -> str:
    """Return the private directory where all local MeshAgent data lives."""
    base = os.path.abspath(os.path.expanduser(
        os.environ.get(HOME_ENV) or "~/.meshagent"))
    os.makedirs(base, mode=0o700, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def config_path() -> str:
    return os.path.join(home(), CONFIG_FILE)


def credentials_path() -> str:
    return os.path.join(home(), CREDENTIALS_FILE)


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_write(path: str, content: str, mode: int = 0o600) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".meshagent-", dir=directory)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        # Persist the rename where the filesystem supports directory fsync.
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def save_json(path: str, value: dict, mode: int = 0o600) -> None:
    _atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n", mode)


def validate_endpoint(value: str) -> str:
    """Validate and canonicalise an API origin without contacting it.

    The API deployment is an origin, not an arbitrary URL.  Rejecting paths,
    credentials, queries and fragments prevents an accidental pasted endpoint
    from silently sending recorder traffic to a surprising location.
    """
    candidate = (value or "").strip().rstrip("/")
    if not candidate:
        raise ConfigurationError("API endpoint is required")
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"}:
        raise ConfigurationError("API endpoint must start with http:// or https://")
    if not parts.hostname or parts.username or parts.password:
        raise ConfigurationError("API endpoint must be an origin without credentials")
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ConfigurationError("API endpoint must not include a path, query, or fragment")
    try:
        port = parts.port
    except ValueError as exc:
        raise ConfigurationError("API endpoint has an invalid port") from exc
    netloc = parts.hostname.lower()
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((parts.scheme.lower(), netloc, "", "", ""))


def load_config() -> dict:
    return _read_json(config_path())


def configured_endpoint() -> str:
    """Configured endpoint, retaining compatibility with older credentials."""
    cfg = load_config()
    candidate = str(cfg.get("api") or "").strip()
    if not candidate:
        candidate = str(load_credentials().get("api") or "").strip()
    if not candidate:
        return DEFAULT_API
    try:
        return validate_endpoint(candidate)
    except ConfigurationError:
        return DEFAULT_API


def endpoint() -> str:
    """Endpoint consumed by every adapter.

    An environment variable remains an explicit per-process override for CI and
    temporary deployments.  It is still validated; an invalid override falls
    back to the durable setting rather than turning a hook into an exception.
    """
    override = os.environ.get(API_ENV, "").strip()
    if override:
        try:
            return validate_endpoint(override)
        except ConfigurationError:
            return configured_endpoint()
    return configured_endpoint()


def endpoint_source() -> str:
    if os.environ.get(API_ENV, "").strip():
        return "environment"
    if load_config().get("api"):
        return "config"
    if load_credentials().get("api"):
        return "legacy credentials"
    return "default"


def set_endpoint(value: str) -> str:
    normalised = validate_endpoint(value)
    cfg = load_config()
    cfg.update({"version": 1, "api": normalised, "updated_at": int(time.time())})
    save_json(config_path(), cfg)
    return normalised


def load_credentials() -> dict:
    return _read_json(credentials_path())


def save_credentials(value: dict) -> None:
    save_json(credentials_path(), value)


def device_token() -> str:
    """Return only the recording device credential, never a delegated read token."""
    value = os.environ.get(DEVICE_TOKEN_ENV, "").strip() or str(
        load_credentials().get("token") or "").strip()
    # A human OIDC token accidentally placed in MESHAGENT_TOKEN must not become
    # a recording credential by fallback.  Device tokens have this fixed prefix.
    return value if value.startswith("mesh_") and "." in value else ""


def read_token() -> str:
    """Return an explicitly delegated/human read credential, if configured."""
    return os.environ.get(READ_TOKEN_ENV, "").strip() or str(
        load_credentials().get("read_token") or "").strip()


def credential_status() -> dict[str, bool]:
    """Token presence only; safe for CLI diagnostics and support tickets."""
    creds = load_credentials()
    return {
        "device": bool(device_token()),
        "read": bool(read_token()),
        "stored_device": bool(str(creds.get("token") or "").strip()),
        "stored_read": bool(str(creds.get("read_token") or "").strip()),
    }


def set_read_token(value: str) -> None:
    token = (value or "").strip()
    if not token:
        raise ConfigurationError("read token is required")
    creds = load_credentials()
    creds["read_token"] = token
    save_credentials(creds)


def clear_read_token() -> None:
    creds = load_credentials()
    creds.pop("read_token", None)
    save_credentials(creds)


def _component(value: str, prefix: str) -> str:
    text = _SAFE.sub("-", (value or "").strip()).strip(".-")[:48] or "unknown"
    digest = hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{text}-{digest}"


def repository_id(path: str | None = None) -> str:
    root = os.path.realpath(os.path.abspath(path or os.environ.get(
        "MESHAGENT_REPOSITORY") or os.getcwd()))
    return _component(root, "repo")


def session_id_component(session_id: str) -> str:
    return _component(session_id, "session")


def _namespace(repository: str | None, editor: str, session_id: str) -> tuple[str, str, str]:
    return repository_id(repository), _component(editor, "editor"), session_id_component(session_id)


def state_path(session_id: str, *, repository: str | None = None,
               editor: str = "unknown") -> str:
    repo, agent, session = _namespace(repository, editor, session_id)
    return os.path.join(home(), STATE_DIR, repo, agent, session + ".json")


def queue_path(session_id: str, *, repository: str | None = None,
               editor: str = "unknown") -> str:
    repo, agent, session = _namespace(repository, editor, session_id)
    return os.path.join(home(), QUEUE_DIR, repo, agent, session + ".jsonl")


@contextlib.contextmanager
def locked(path: str) -> Iterator[None]:
    """An advisory process lock adjacent to a state or queue file."""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(lock_path), mode=0o700, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_session(session_id: str, *, repository: str | None = None,
                 editor: str = "unknown") -> dict:
    path = state_path(session_id, repository=repository, editor=editor)
    with locked(path):
        return _read_json(path)


def write_session(session_id: str, value: dict, *, repository: str | None = None,
                  editor: str = "unknown") -> None:
    path = state_path(session_id, repository=repository, editor=editor)
    body = dict(value)
    body.setdefault("session_id", session_id)
    body.setdefault("repository", os.path.realpath(os.path.abspath(
        repository or os.environ.get("MESHAGENT_REPOSITORY") or os.getcwd())))
    body.setdefault("editor", editor)
    with locked(path):
        save_json(path, body)


def update_session(session_id: str, update, *, repository: str | None = None,
                   editor: str = "unknown") -> dict:
    """Atomically read, transform, and replace session state."""
    path = state_path(session_id, repository=repository, editor=editor)
    with locked(path):
        state = _read_json(path)
        value = update(dict(state))
        value = state if value is None else value
        value.setdefault("session_id", session_id)
        value.setdefault("repository", os.path.realpath(os.path.abspath(
            repository or os.environ.get("MESHAGENT_REPOSITORY") or os.getcwd())))
        value.setdefault("editor", editor)
        save_json(path, value)
        return value


def _parse_lines(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    batches: list[dict] = []
    for line in lines:
        try:
            item = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            batches.append(item)
    return batches


def _queue_contents(batches: list[dict]) -> str:
    return "".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                   for item in batches)


def _retained(batches: list[dict]) -> list[dict]:
    """Keep a bounded suffix of batches and bytes without corrupting JSONL."""
    out: list[dict] = []
    total = 0
    for batch in reversed(batches[-max_queue_batches():]):
        encoded = json.dumps(batch, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if len(encoded) > max_queue_bytes():
            continue
        if total + len(encoded) > max_queue_bytes():
            break
        out.append(batch)
        total += len(encoded)
    out.reverse()
    return out


def enqueue(batch: dict, *, repository: str | None = None,
            editor: str | None = None, session_id: str | None = None) -> None:
    agent = editor or str(batch.get("agent") or "unknown")
    session = session_id or str(batch.get("session") or "")
    if not session:
        return
    path = queue_path(session, repository=repository, editor=agent)
    with locked(path):
        entries = _retained(_parse_lines(path) + [batch])
        _atomic_write(path, _queue_contents(entries))


def drain(*, repository: str | None = None, editor: str = "unknown",
          session_id: str) -> list[dict]:
    """Atomically take a queue.  Concurrent enqueues land in a fresh file."""
    path = queue_path(session_id, repository=repository, editor=editor)
    with locked(path):
        batches = _parse_lines(path)
        if batches or os.path.exists(path):
            _atomic_write(path, "")
        return batches


def drain_matching(*, repository: str | None = None, editor: str = "unknown") -> list[dict]:
    """Drain every queue for one repository/editor namespace in path order."""
    repo, agent, _ = _namespace(repository, editor, "placeholder")
    root = Path(home()) / QUEUE_DIR / repo / agent
    if not root.exists():
        return []
    batches: list[dict] = []
    for path in sorted(root.glob("*.jsonl")):
        batches.extend(drain_path(str(path)))
    return batches


def queue_files() -> list[str]:
    root = Path(home()) / QUEUE_DIR
    if not root.exists():
        return []
    return sorted(str(path) for path in root.rglob("*.jsonl") if path.is_file())


def drain_path(path: str) -> list[dict]:
    with locked(path):
        batches = _parse_lines(path)
        if batches or os.path.exists(path):
            _atomic_write(path, "")
        return batches


def enqueue_path(path: str, batches: list[dict]) -> None:
    if not batches:
        return
    with locked(path):
        entries = _retained(_parse_lines(path) + batches)
        _atomic_write(path, _queue_contents(entries))


def queue_summary() -> tuple[int, int]:
    files = queue_files()
    return len(files), sum(len(_parse_lines(path)) for path in files)


def session_files() -> list[str]:
    root = Path(home()) / STATE_DIR
    if not root.exists():
        return []
    return sorted(str(path) for path in root.rglob("*.json") if path.is_file())


def sessions(*, repository: str | None = None) -> list[tuple[str, dict]]:
    """Return readable namespaced sessions, optionally for one repository."""
    wanted = repository_id(repository) if repository else ""
    records: list[tuple[str, dict]] = []
    for path in session_files():
        if wanted and f"{os.sep}{wanted}{os.sep}" not in path:
            continue
        with locked(path):
            value = _read_json(path)
        if value:
            records.append((path, value))
    return records


def event_key(agent: str, session_id: str, event: dict, *, repository: str | None = None) -> str:
    """Stable event key for retries; deliberately excludes transient clocks."""
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    material = "\x1f".join((repository_id(repository), agent, session_id, canonical))
    return "evt_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def batch_key(batch: dict) -> str:
    canonical = json.dumps(batch, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "batch_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def add_event_keys(agent: str, session_id: str, events: list[dict], *,
                   repository: str | None = None) -> list[dict]:
    keyed: list[dict] = []
    for event in events:
        item = dict(event)
        item.setdefault("idempotency_key", event_key(agent, session_id, item,
                                                       repository=repository))
        keyed.append(item)
    return keyed


def safe_endpoint_for_display() -> str:
    """Endpoint alone is diagnostic-safe because validation disallows userinfo."""
    return endpoint()


def permissions(path: str) -> str:
    try:
        return oct(os.stat(path).st_mode & 0o777)
    except OSError:
        return "missing"


def diagnostic() -> dict:
    queues, batches = queue_summary()
    return {
        "home": home(),
        "home_mode": permissions(home()),
        "endpoint": safe_endpoint_for_display(),
        "endpoint_source": endpoint_source(),
        "config_present": os.path.exists(config_path()),
        "credentials_present": os.path.exists(credentials_path()),
        "credentials_mode": permissions(credentials_path()),
        "queues": queues,
        "queued_batches": batches,
        "sessions": len(session_files()),
        "credentials": credential_status(),
    }
