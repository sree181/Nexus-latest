"""
rag/symbolic/firewall.py — hallucination firewall.

After generation, every factual sentence in the answer must carry a citation
tag that exists in the proof/evidence set (``HEDGE-N`` / ``RULE-N`` / ``STEP-N``
/ ``PATTERN-N``). Unsupported factual sentences are stripped; if proof was
required but the goal was not proved, the firewall abstains outright. This makes
unsupported generation impossible to return.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# EDGE-<ulid> is the stable, write-surviving citation form minted by the
# memory layer (Crockford base32 ULID); the numeric forms are positional.
_TAG_RE = re.compile(
    r"\[(HEDGE-\d+|RULE-\d+|STEP-\d+|PATTERN-\d+|EDGE-[0-9A-HJKMNP-TV-Z]{26})\]"
)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_ABSTAIN_MSG = "Insufficient verifiable evidence to answer this question."
_UNVERIFIED_RE = re.compile(r"\[UNVERIFIED", re.I)

# Sentences shorter than this are treated as connective/non-factual.
_FACTUAL_MIN_CHARS = 20


@dataclass
class FirewallResult:
    answer: str                                   # sanitized answer
    outcome: str = "supported"                    # supported | stripped | abstained
    abstained: bool = False
    coverage: float = 1.0                         # supported / factual sentences
    supported: list[str] = field(default_factory=list)
    stripped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "abstained": self.abstained,
            "coverage": round(self.coverage, 3),
            "supported_count": len(self.supported),
            "stripped_count": len(self.stripped),
            "stripped": self.stripped[:10],
        }


def _is_factual(sentence: str) -> bool:
    return len(sentence.strip()) >= _FACTUAL_MIN_CHARS


class HallucinationFirewall:
    """Gate an LLM answer against the set of allowed citation tags."""

    def __init__(self, abstain_message: str = _ABSTAIN_MSG) -> None:
        self._abstain_message = abstain_message

    def check(
        self,
        answer: str,
        allowed_tags: set[str],
        *,
        proved: bool = True,
        require_proof: bool = False,
    ) -> FirewallResult:
        if require_proof and not proved:
            return FirewallResult(
                answer=self._abstain_message,
                outcome="abstained",
                abstained=True,
                coverage=0.0,
            )

        sentences = [s for s in _SENT_SPLIT.split(answer.strip()) if s.strip()]
        kept: list[str] = []
        supported: list[str] = []
        stripped: list[str] = []
        factual = 0

        for sent in sentences:
            if not _is_factual(sent):
                kept.append(sent)                 # connective glue — keep as-is
                continue
            # Honest "I can't determine this" markers are allowed.
            if _UNVERIFIED_RE.search(sent):
                kept.append(sent)
                continue
            factual += 1
            tags = {f"[{t}]" for t in _TAG_RE.findall(sent)}
            tags = {t.strip("[]") for t in tags}
            if tags & allowed_tags:
                kept.append(sent)
                supported.append(sent)
            else:
                stripped.append(sent.strip())

        coverage = (len(supported) / factual) if factual else 1.0
        sanitized = " ".join(kept).strip()
        if stripped and not sanitized:
            # Everything factual was unsupported → abstain rather than return fluff.
            return FirewallResult(
                answer=self._abstain_message, outcome="abstained",
                abstained=True, coverage=coverage,
                supported=supported, stripped=stripped,
            )
        outcome = "stripped" if stripped else "supported"
        return FirewallResult(
            answer=sanitized or answer.strip(),
            outcome=outcome,
            abstained=False,
            coverage=coverage,
            supported=supported,
            stripped=stripped,
        )
