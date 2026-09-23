"""Whether an agent may install this package, and why.

Everything else in MeshAgent observes. This is the one place that refuses,
and that changes what honesty costs. A screen that overstates a risk wastes
somebody's afternoon; a gate that overstates one stops a developer working,
and a gate that understates one waves through the thing it exists to catch.

So the verdict is four-valued, and the fourth value is the important one:

    allow    nothing known against this version
    warn     something is known, below the bar this deployment set
    block    an advisory at or above that bar
    unknown  the feeds could not be reached

`unknown` is not `allow`. A gate that cannot see OSV and answers "fine" is
worse than no gate, because it manufactures the impression of a check. What
a deployment does about not knowing is a policy choice -- MESHAGENT_GATE_ON_
UNKNOWN -- and it defaults to letting the install through, because the
alternative is that an OSV outage stops every developer in the company. The
decision is recorded either way, so ungated installs are countable rather
than invisible.

The policy is read from the environment rather than hardcoded, and stated
back on every decision, so the answer to "why was I blocked" is on the
response rather than in this file.

Engine-free on purpose, like advisories.py: this is a function of a package,
a version and a policy. The gateways add fleet context around it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import advisories

# Ranked so a threshold can be compared rather than matched. "unknown" sits
# at the bottom: an advisory whose severity nobody scored is still an
# advisory, but it is not grounds for blocking on its own.
RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

DEFAULT_THRESHOLD = "high"

# Licenses a deployment refuses outright. Empty by default: which licenses a
# company can accept is a legal question, not one this file should answer.
LICENSE_ENV = "MESHAGENT_DENIED_LICENSES"


@dataclass(frozen=True)
class Policy:
    """What this deployment decided to refuse."""

    # Advisories at or above this severity block. Below it, they warn.
    threshold: str = DEFAULT_THRESHOLD
    denied_licenses: tuple[str, ...] = ()
    # What to do when the feeds are unreachable. False -- the default --
    # lets the install through and records that it was not checked. True
    # fails closed, which is right for a regulated estate and wrong for a
    # company that would rather not stop work during an OSV outage.
    block_on_unknown: bool = False

    @property
    def described(self) -> str:
        """The policy in a sentence, carried on every decision so a blocked
        developer can read the rule rather than be told to file a ticket."""
        parts = [f"advisories of {self.threshold} severity or above are "
                 f"refused"]
        if self.denied_licenses:
            parts.append(f"licenses {', '.join(self.denied_licenses)} are "
                         f"refused")
        parts.append("an unreachable advisory feed "
                     + ("refuses the install"
                        if self.block_on_unknown
                        else "lets it through, recorded as unchecked"))
        return "; ".join(parts)


def _split(raw: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def policy() -> Policy:
    threshold = os.environ.get("MESHAGENT_GATE_THRESHOLD",
                               DEFAULT_THRESHOLD).strip().lower()
    return Policy(
        threshold=threshold if threshold in RANK else DEFAULT_THRESHOLD,
        denied_licenses=_split(os.environ.get(LICENSE_ENV, "")),
        block_on_unknown=os.environ.get("MESHAGENT_GATE_ON_UNKNOWN", "")
        in ("1", "true", "yes"),
    )


@dataclass
class Decision:
    """The answer, and everything it rests on."""

    package: str
    version: str
    verdict: str                        # allow | warn | block | unknown
    reasons: list[str]
    advisories: list[advisories.Advisory]
    worst: str | None = None            # worst severity seen, if any
    unavailable: str | None = None      # why this is incomplete, verbatim


def check(package: str, version: str, pol: Policy | None = None,
          resolve=advisories.resolve_at) -> Decision:
    """Decide one install.

    `resolve` is injected so a test can state exactly what the feed said
    rather than depend on what OSV happens to hold this week -- which is a
    real dependency to avoid in a file whose job is to refuse things."""
    pol = pol or policy()
    version = version.strip()

    if not version:
        # An unpinned install is not a thing that can be checked: whatever
        # resolves today is not necessarily what resolves in CI tomorrow.
        # Refusing to judge it is more honest than judging the current
        # release and implying the pin was examined. It is the same kind of
        # not-knowing as an unreachable feed, so the same policy decides it.
        return Decision(
            package=package, version="",
            verdict="block" if pol.block_on_unknown else "unknown",
            reasons=[f"{package} was not pinned to a version, so there is "
                     "nothing specific to check against"],
            advisories=[], unavailable="no version given")

    found = resolve(package, version)
    hits = found.advisories
    worst = max((a.severity for a in hits), key=lambda s: RANK.get(s, 0),
                default=None)

    reasons: list[str] = []
    verdict = "allow"

    if found.unavailable:
        verdict = "block" if pol.block_on_unknown else "unknown"
        reasons.append(f"{found.unavailable}, so this install was not checked")

    blocking = [a for a in hits
                if RANK.get(a.severity, 0) >= RANK.get(pol.threshold, 3)]
    if blocking:
        verdict = "block"
        for adv in blocking:
            reasons.append(
                f"{adv.id} ({adv.severity}) affects {package} {version}"
                + (f" [{adv.cwe}]" if adv.cwe else "")
                + f": {adv.summary}")
    elif hits and verdict == "allow":
        verdict = "warn"
        reasons.append(
            f"{len(hits)} advisory(ies) affect {package} {version}, none at "
            f"{pol.threshold} or above: "
            + ", ".join(f"{a.id} ({a.severity})" for a in hits))

    if found.license and pol.denied_licenses:
        if any(d.lower() in found.license.lower()
               for d in pol.denied_licenses):
            verdict = "block"
            reasons.append(
                f"{package} is licensed {found.license}, which this "
                "deployment does not accept")

    if not reasons:
        reasons.append(
            f"No advisory in OSV affects {package} {version}. That is the "
            "feed's silence, not a guarantee.")

    return Decision(package=package, version=version, verdict=verdict,
                    reasons=reasons, advisories=hits, worst=worst,
                    unavailable=found.unavailable)
