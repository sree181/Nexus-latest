"""Identity for things that cannot open a browser.

The recorder's whole value is that it runs without being asked: an editor
hook fires on every write, which is what makes coverage a property of the
system rather than of the agent's goodwill. But a hook is a shell command.
It has no browser, so the OIDC redirect the UI uses cannot serve it, and
until now it fell back to asserting a name in a header -- which means the
coverage report names developers against gaps on the strength of a string
anyone can type into curl.

A device token fixes the attribution. It is minted by a human who *did*
authenticate, carries that human's identity, and is the only credential the
hook ever holds.

Three properties it is worth being explicit about:

*It is scoped to recording, and the scope is not configurable.* A token
sitting in plain text in a developer's home directory on a laptop is a
token that will eventually be read by something else. Recording-only means
the worst case is fabricated memory -- bad, visible in the audit log, and
recoverable -- rather than read access to every run in the fleet. The
allowance is a fixed set here rather than a field, because a scope that can
be widened is a scope that will be widened by whoever mints the token.

*The secret is never stored.* Only a SHA-256 of it, so this file leaking
does not hand over working tokens.

*It is never more trustworthy than the person who minted it.* In local mode
there is no identity provider, the minting human was merely asserted, and
the token inherits `verified=False` all the way through to the audit line.
A bearer secret does not make an unverified claim verified, and nothing
here pretends otherwise.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from functools import wraps
from typing import Any

from .auth import AuthError, Principal, Role

STORE = "devices.json"

VERSION = 1

# Everything a device token is allowed to reach. Recording memory, and asking
# the gate about a package -- the two things a hook does. Notably absent:
# reading any run, forgetting anything, and every fleet view.
SCOPE = ("recorder.write", "gate.check")

PREFIX = "mesh_"

# How long a human has to approve a pairing before the CLI has to start over.
# Short because an unapproved code is a live phishing target: the attack on
# every device flow is to start a pairing and talk someone else into
# approving it.
PAIRING_TTL = 600

# User codes are read off one screen and typed into another, so the alphabet
# omits the characters people get wrong doing that: O/0, I/1/L, U/V.
ALPHABET = "ABCDEFGHJKMNPQRSTWXYZ23456789"
CODE_LEN = 8


def _synchronized(method):
    """Serialize one device-store operation across FastAPI worker threads."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return guarded


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _user_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    return f"{raw[:4]}-{raw[4:]}"


@dataclass
class Device:
    """One registered machine, and the human it records as."""

    id: str
    label: str                  # what the developer called it: "work laptop"
    subject: str                # the human this token records as
    name: str
    email: str
    role: Role
    verified: bool              # was the minting human verified
    secret_hash: str
    created_at: int
    last_used: int = 0
    revoked_at: int = 0

    @property
    def active(self) -> bool:
        return self.revoked_at == 0

    def principal(self) -> Principal:
        """Who this token acts as.

        The role is carried from the minting human but is nearly inert: a
        device token cannot reach a single route that checks for analyst, so
        an analyst's hook has exactly a developer's reach. It is kept only so
        the audit line says which person's laptop this was."""
        return Principal(subject=self.subject, name=self.name,
                         email=self.email, role=self.role,
                         verified=self.verified, device=self.id)


@dataclass
class Pairing:
    """A login in progress: the CLI is waiting, the human has not approved."""

    device_code_hash: str
    user_code: str
    label: str
    started_at: int
    # Filled in when a human approves. Until then this pairing mints nothing.
    subject: str = ""
    name: str = ""
    email: str = ""
    role: Role = "developer"
    verified: bool = False
    approved: bool = False
    claimed: bool = False

    def expired(self, now: int | None = None) -> bool:
        return (now or int(time.time())) - self.started_at > PAIRING_TTL


@dataclass
class Store:
    """Devices and in-flight pairings, held as JSON under `base`.

    Pairings are persisted rather than kept in a process dictionary so that
    an API restart -- or a second worker taking the poll -- does not strand a
    developer halfway through `meshagent login` with no way to tell why."""

    base: str
    devices: dict[str, Device] = field(default_factory=dict)
    pairings: dict[str, Pairing] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def path(self) -> str:
        return os.path.join(self.base, STORE)

    # -- persistence ----------------------------------------------------------

    @_synchronized
    def save(self) -> None:
        os.makedirs(self.base, exist_ok=True)
        body = {
            "version": VERSION,
            "devices": [asdict(d) for d in self.devices.values()],
            "pairings": [asdict(p) for p in self.pairings.values()],
        }
        fd, tmp = tempfile.mkstemp(dir=self.base, prefix=".devices-")
        try:
            # 0600 before anything is written to it: this file holds the
            # hashes and the whole point is that it is not world-readable.
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(body, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            directory = os.open(self.base, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- pairing (the device authorization grant, in shape) -------------------

    @_synchronized
    def start(self, label: str) -> tuple[str, str]:
        """Begin a login. Returns (device_code, user_code).

        The device code is the secret the CLI polls with and is never shown
        to the human; the user code is the short string they read out and
        approve. Keeping them separate is what stops someone who shoulder-
        surfed the screen from claiming the token."""
        self.sweep()
        device_code = secrets.token_urlsafe(32)
        pairing = Pairing(
            device_code_hash=_digest(device_code),
            user_code=_user_code(),
            label=label.strip()[:80] or "unnamed device",
            started_at=int(time.time()),
        )
        self.pairings[pairing.user_code] = pairing
        self.save()
        return device_code, pairing.user_code

    @_synchronized
    def pending(self, user_code: str) -> Pairing:
        """The pairing a human is being asked to approve."""
        pairing = self.pairings.get(user_code.strip().upper())
        if pairing is None or pairing.expired():
            raise AuthError("no pairing with that code")
        return pairing

    @_synchronized
    def approve(self, user_code: str, who: Principal) -> Pairing:
        """A human says yes: bind the pairing to them.

        Refused for a caller that is itself a device, so a leaked recording
        token cannot quietly mint a second one and outlive its own
        revocation."""
        if who.device:
            raise AuthError("a device token cannot approve another device")
        pairing = self.pending(user_code)
        if pairing.approved:
            raise AuthError("that pairing was already approved")
        pairing.subject = who.subject
        pairing.name = who.name
        pairing.email = who.email
        pairing.role = who.role
        pairing.verified = who.verified
        pairing.approved = True
        self.save()
        return pairing

    @_synchronized
    def claim(self, device_code: str) -> tuple[Device, str] | None:
        """The CLI collecting its token. None while nobody has approved yet.

        The pairing is consumed on success: a device code that has already
        produced a token produces nothing further, so replaying a poll that
        was captured in transit yields an error rather than a second
        credential for the same machine."""
        wanted = _digest(device_code)
        for pairing in list(self.pairings.values()):
            if not hmac.compare_digest(pairing.device_code_hash, wanted):
                continue
            if pairing.expired():
                del self.pairings[pairing.user_code]
                self.save()
                raise AuthError("this login took too long; start again")
            if pairing.claimed:
                raise AuthError("this login was already completed")
            if not pairing.approved:
                return None
            pairing.claimed = True
            device, token = self._mint(pairing)
            del self.pairings[pairing.user_code]
            self.save()
            return device, token
        raise AuthError("unknown login")

    def _mint(self, pairing: Pairing) -> tuple[Device, str]:
        device_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        device = Device(
            id=device_id, label=pairing.label, subject=pairing.subject,
            name=pairing.name, email=pairing.email, role=pairing.role,
            verified=pairing.verified, secret_hash=_digest(secret),
            created_at=int(time.time()),
        )
        self.devices[device_id] = device
        return device, f"{PREFIX}{device_id}.{secret}"

    # -- using one -----------------------------------------------------------

    @_synchronized
    def verify(self, token: str) -> Principal:
        """The human behind a device token, or AuthError.

        Every failure says the same thing. Distinguishing "no such device"
        from "wrong secret" would turn this into an oracle for enumerating
        which developers have registered machines."""
        if not token.startswith(PREFIX) or "." not in token:
            raise AuthError("not a device token")
        device_id, _, secret = token[len(PREFIX):].partition(".")
        device = self.devices.get(device_id)
        rejected = AuthError("device token rejected")
        if device is None or not device.active:
            raise rejected
        if not hmac.compare_digest(device.secret_hash, _digest(secret)):
            raise rejected
        now = int(time.time())
        # Written at most once a minute. A hook fires on every file write, and
        # fsyncing this file that often would make the editor wait on us.
        if now - device.last_used > 60:
            device.last_used = now
            self.save()
        return device.principal()

    @_synchronized
    def revoke(self, device_id: str, who: Principal) -> Device:
        """Retire a device. Its own owner, or the security office."""
        device = self.devices.get(device_id)
        if device is None or not device.active:
            raise AuthError("no such device")
        if not who.has("device.fleet") and device.subject != who.subject:
            raise AuthError("no such device")    # not whose it is to revoke
        device.revoked_at = int(time.time())
        self.save()
        return device

    @_synchronized
    def owned_by(self, who: Principal) -> list[Device]:
        """Devices this caller may see: their own, or all of them for the
        security office, whose job is to know what is recording."""
        return sorted(
            (d for d in self.devices.values()
             if d.active and (who.has("device.fleet") or d.subject == who.subject)),
            key=lambda d: d.created_at, reverse=True)

    @_synchronized
    def sweep(self) -> None:
        """Drop pairings nobody completed. An expired code left lying around
        is one more thing a phisher can try to get approved."""
        now = int(time.time())
        stale = [c for c, p in self.pairings.items() if p.expired(now)]
        for code in stale:
            del self.pairings[code]


def load(base: str) -> Store:
    """The device store at `base`, or an empty one.

    A store that will not parse is treated as empty, which fails closed:
    every existing token stops working and developers run `meshagent login`
    again. The alternative -- guessing at half-read records -- would be
    guessing about who is allowed to write to memory."""
    store = Store(base=base)
    try:
        with open(store.path) as fh:
            body: dict[str, Any] = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return store
    if body.get("version") != VERSION:
        return store
    for raw in body.get("devices", []):
        try:
            store.devices[raw["id"]] = Device(**raw)
        except (TypeError, KeyError):
            continue
    for raw in body.get("pairings", []):
        try:
            pairing = Pairing(**raw)
        except (TypeError, KeyError):
            continue
        store.pairings[pairing.user_code] = pairing
    store.sweep()
    return store
