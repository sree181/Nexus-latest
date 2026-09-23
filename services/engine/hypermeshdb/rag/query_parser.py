"""
rag/query_parser.py — Natural language query parser for HyperGraphRAG

Extracts structured parameters from a free-text query without requiring
an LLM call. Uses regex, keyword classification, and entity-map lookup.

Output
------
ParsedQuery:
    entities:     list of (label, node_id | None) — entities named in query
    time_start:   epoch int | None
    time_end:     epoch int | None
    intent:       "threat" | "formation" | "cluster" | "general"
    raw_filters:  dict of extra constraints parsed from query
    raw_query:    original text
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ParsedQuery:
    raw_query:    str
    entities:     list[tuple[str, int | None]]   = field(default_factory=list)
    time_start:   int | None                      = None
    time_end:     int | None                      = None
    intent:       str                             = "general"
    raw_filters:  dict[str, Any]                  = field(default_factory=dict)
    keywords:     list[str]                       = field(default_factory=list)

    def has_time_window(self) -> bool:
        return self.time_start is not None or self.time_end is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_query":   self.raw_query,
            "entities":    [{"label": l, "node_id": n} for l, n in self.entities],
            "time_start":  self.time_start,
            "time_end":    self.time_end,
            "intent":      self.intent,
            "raw_filters": self.raw_filters,
            "keywords":    self.keywords,
        }


# ── Intent keyword maps ───────────────────────────────────────────────────────

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "threat": [
        "attack", "threat", "malware", "lateral", "movement", "c2", "beacon",
        "exfiltration", "compromise", "breach", "intrusion", "ransomware",
        "credential", "pivot", "process", "injection", "persistence",
        "mitre", "technique", "alert", "incident", "ioc", "hash",
    ],
    "formation": [
        "formation", "swarm", "drone", "coalition", "cluster", "collision",
        "proximity", "fleet", "maneuver", "position", "battery", "altitude",
    ],
    "cluster": [
        "weed", "plant", "species", "regrowth", "grid", "ndvi", "crop",
        "field", "laser", "meristem", "herbicide", "segment",
    ],
    "temporal": [
        "when", "timeline", "evolve", "trend", "change", "spike", "burst",
        "over time", "history", "progression", "pattern",
    ],
}

_SEVERITY_KEYWORDS = {
    "critical": ["critical", "severe", "urgent", "immediate"],
    "high":     ["high", "significant", "important", "major"],
    "medium":   ["medium", "moderate", "notable"],
    "low":      ["low", "minor", "informational"],
}


# ── Relative time expressions ─────────────────────────────────────────────────

_TIME_PATTERNS: list[tuple[re.Pattern, Any]] = [
    # "last N hours/days/weeks/minutes"
    (re.compile(r"last\s+(\d+)\s+(minute|hour|day|week|month)s?", re.I),
     lambda m: _relative_delta(int(m.group(1)), m.group(2).lower())),
    # "past N hours/days/weeks"
    (re.compile(r"past\s+(\d+)\s+(minute|hour|day|week|month)s?", re.I),
     lambda m: _relative_delta(int(m.group(1)), m.group(2).lower())),
    # "today" / "yesterday"
    (re.compile(r"\btoday\b", re.I),      lambda m: _today_range()),
    (re.compile(r"\byesterday\b", re.I),  lambda m: _yesterday_range()),
    # "last week" / "this week"
    (re.compile(r"\blast\s+week\b", re.I), lambda m: _last_week_range()),
    (re.compile(r"\bthis\s+week\b", re.I), lambda m: _this_week_range()),
    # "last month"
    (re.compile(r"\blast\s+month\b", re.I), lambda m: _relative_delta(30, "day")),
    # ISO-like: "since 2024-01-15" or "after 2024-01-15"
    (re.compile(r"(?:since|after)\s+(\d{4}-\d{2}-\d{2})", re.I),
     lambda m: _since_date(m.group(1))),
]


def _now() -> int:
    return int(time.time())


def _relative_delta(n: int, unit: str) -> tuple[int, int]:
    seconds = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000}
    delta = n * seconds.get(unit, 86400)
    now   = _now()
    return (now - delta, now)


def _today_range() -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (int(start.timestamp()), int(now.timestamp()))


def _yesterday_range() -> tuple[int, int]:
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(days=1)
    return (int(start.timestamp()), int(end.timestamp()))


def _last_week_range() -> tuple[int, int]:
    now = _now()
    return (now - 7 * 86400, now)


def _this_week_range() -> tuple[int, int]:
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=now.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return (int(start.timestamp()), int(now.timestamp()))


def _since_date(date_str: str) -> tuple[int, int]:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (int(dt.timestamp()), _now())
    except ValueError:
        return (_now() - 7 * 86400, _now())


# ── QueryParser ───────────────────────────────────────────────────────────────

class QueryParser:
    """
    Parses a natural language query into structured retrieval parameters.

    Parameters
    ----------
    entity_map :
        Dict ``{str(node_id): {type, display, short, raw}}`` from the DB.
        Used for entity name → node_id resolution.
    """

    def __init__(self, entity_map: dict[str, Any] | None = None) -> None:
        self._em = entity_map or {}
        # Build reverse lookup: normalised label → node_id
        self._label_to_id: dict[str, int] = {}
        for nid, meta in self._em.items():
            for key in ("display", "short", "raw"):
                val = meta.get(key, "")
                if val:
                    self._label_to_id[val.lower()] = int(nid)
                    # Also index the last segment of backslash paths
                    if "\\" in val:
                        self._label_to_id[val.split("\\")[-1].lower()] = int(nid)

    # ── Public API ────────────────────────────────────────────────────────────

    def parse(self, query_text: str) -> ParsedQuery:
        text = query_text.strip()

        entities    = self._extract_entities(text)
        time_window = self._extract_time(text)
        intent      = self._classify_intent(text)
        keywords    = self._extract_keywords(text)
        filters     = self._extract_filters(text)

        return ParsedQuery(
            raw_query  = text,
            entities   = entities,
            time_start = time_window[0] if time_window else None,
            time_end   = time_window[1] if time_window else None,
            intent     = intent,
            raw_filters= filters,
            keywords   = keywords,
        )

    # ── Entity extraction ─────────────────────────────────────────────────────

    def _extract_entities(self, text: str) -> list[tuple[str, int | None]]:
        found: list[tuple[str, int | None]] = []
        seen: set[str] = set()

        # Direct entity map lookup (case-insensitive substring match)
        text_lower = text.lower()
        # Sort by length descending to prefer longer (more specific) matches
        sorted_labels = sorted(self._label_to_id.items(), key=lambda x: -len(x[0]))
        for label_lower, node_id in sorted_labels:
            if len(label_lower) < 3:
                continue
            if label_lower in text_lower and label_lower not in seen:
                # Get display label
                meta  = self._em.get(str(node_id), {})
                display = meta.get("short") or meta.get("display") or label_lower
                found.append((display, node_id))
                seen.add(label_lower)
                if len(found) >= 10:
                    break

        # Regex patterns for entity types not in entity map
        patterns = [
            # IP addresses
            (r'\b(?:\d{1,3}\.){3}\d{1,3}\b',          "ip"),
            # Windows hostnames (CAPS or MACHINE-N format)
            (r'\b[A-Z][A-Z0-9\-]{3,30}\b',             "machine"),
            # Process names (ending in .exe, .sh, .py)
            (r'\b\w+\.(?:exe|sh|py|dll|ps1)\b',        "process"),
            # Account-like patterns
            (r'\bAccount[-_]?\w+\b',                    "account"),
            # Node-N patterns
            (r'\b(?:node|machine|process|drone)[-_]?\d+\b', "entity"),
        ]
        for pattern, _ in patterns:
            for m in re.finditer(pattern, text, re.I):
                label = m.group(0)
                label_lower = label.lower()
                if label_lower not in seen and len(label) >= 3:
                    # Try to resolve to a node_id
                    nid = self._label_to_id.get(label_lower)
                    found.append((label, nid))
                    seen.add(label_lower)

        return found[:12]

    # ── Time extraction ───────────────────────────────────────────────────────

    def _extract_time(self, text: str) -> tuple[int, int] | None:
        for pattern, handler in _TIME_PATTERNS:
            m = pattern.search(text)
            if m:
                result = handler(m)
                if isinstance(result, tuple) and len(result) == 2:
                    return result
        return None

    # ── Intent classification ─────────────────────────────────────────────────

    def _classify_intent(self, text: str) -> str:
        text_lower = text.lower()
        scores: dict[str, int] = {k: 0 for k in _INTENT_KEYWORDS}
        for intent, kws in _INTENT_KEYWORDS.items():
            scores[intent] = sum(1 for kw in kws if kw in text_lower)
        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0 else "general"

    # ── Keyword extraction ────────────────────────────────────────────────────

    def _extract_keywords(self, text: str) -> list[str]:
        stop = {
            "what", "which", "where", "when", "who", "how", "why",
            "did", "does", "do", "is", "are", "was", "were", "the",
            "a", "an", "in", "on", "at", "to", "of", "for", "and",
            "or", "but", "with", "by", "from", "that", "this", "any",
            "all", "has", "have", "had", "been", "be", "can", "could",
            "would", "should", "will", "show", "me", "list", "get",
            "find", "tell", "give", "past", "last", "over",
        }
        words = re.findall(r'\b[a-zA-Z]\w{2,}\b', text.lower())
        return [w for w in words if w not in stop][:20]

    # ── Filter extraction ─────────────────────────────────────────────────────

    def _extract_filters(self, text: str) -> dict[str, Any]:
        filters: dict[str, Any] = {}

        # Severity filter
        for sev, kws in _SEVERITY_KEYWORDS.items():
            if any(kw in text.lower() for kw in kws):
                filters["min_severity"] = sev
                break

        # Min weight
        m = re.search(r'weight\s*[>≥]\s*([\d.]+)', text, re.I)
        if m:
            filters["min_weight"] = float(m.group(1))

        # Top-N
        m = re.search(r'top[- ](\d+)', text, re.I)
        if m:
            filters["top_n"] = int(m.group(1))

        return filters
