"""
Python-side boolean ``WHERE`` evaluation.

The C parser stores ``WHERE`` predicates as a *flat AND list* and pushes
``event_ts`` ranges / ``members`` lookups down to the TPI/FMI indexes. That
grammar cannot express ``OR``, ``NOT``, parentheses, or ``<>`` / ``!=``.

Rather than rebuild the compiled predicate representation (and its index
pushdown) into a boolean tree, we handle these *richer* single-table
``MATCH HYPEREDGE`` predicates here, in Python — the same layer that already
evaluates property predicates (`_connection._apply_predicates`). The query's
``WHERE`` is parsed into a small boolean expression tree, stripped from the text
handed to the C parser (which then performs a full table scan), and evaluated
per row during post-processing.

Scope (this increment): single-pattern ``MATCH HYPEREDGE`` only. Joins and DML
(`DELETE` / `UPDATE`) keep the C AND-only path. ``members`` / ``IN`` predicates
inside a boolean expression are not yet supported and raise
:class:`WhereParseError`.

Trade-off: because the boolean predicate is evaluated Python-side, an ``OR`` /
``NOT`` / ``<>`` query performs a full table scan (TPI/PSI pushdown is not
applied to disjunctive predicates). Correctness first; pushdown is a future
optimisation.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "WhereParseError",
    "Node",
    "Comparison",
    "Not",
    "And",
    "Or",
    "compile_where",
    "evaluate",
    "extract_bool_where",
]


class WhereParseError(ValueError):
    """Raised when a ``WHERE`` clause cannot be parsed into a boolean tree."""


# ── Tokeniser ─────────────────────────────────────────────────────────────────

_KEYWORDS = {"AND", "OR", "NOT", "WHERE", "RETURN", "ORDER", "BY", "LIMIT",
             "MATCH", "HYPEREDGE", "MEMBERS", "IN", "ASC", "DESC", "AS"}
_TWO_CHAR_OPS = {">=", "<=", "<>", "!="}
_ONE_CHAR_OPS = {"=", ">", "<"}


@dataclass
class _Tok:
    kind: str   # 'ident' | 'num' | 'str' | 'op' | 'lparen' | 'rparen' | 'dot' | 'kw'
    text: str
    pos: int    # start offset into the source string


def _tokenize(s: str) -> list[_Tok]:
    toks: list[_Tok] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        # string literal (single or double quoted; no escapes — matches C lexer)
        if c in ("'", '"'):
            quote = c
            j = i + 1
            buf = []
            while j < n and s[j] != quote:
                buf.append(s[j])
                j += 1
            if j >= n:
                raise WhereParseError("unterminated string literal")
            toks.append(_Tok("str", "".join(buf), i))
            i = j + 1
            continue
        # signed / unsigned number (no arithmetic in WHERE, so leading '-' is a sign)
        if c.isdigit() or (c == "-" and i + 1 < n and s[i + 1].isdigit()):
            j = i + 1
            seen_dot = False
            while j < n and (s[j].isdigit() or (s[j] == "." and not seen_dot)):
                if s[j] == ".":
                    seen_dot = True
                j += 1
            toks.append(_Tok("num", s[i:j], i))
            i = j
            continue
        # two-char operators first
        two = s[i:i + 2]
        if two in _TWO_CHAR_OPS:
            toks.append(_Tok("op", "<>" if two == "!=" else two, i))
            i += 2
            continue
        if c in _ONE_CHAR_OPS:
            toks.append(_Tok("op", c, i))
            i += 1
            continue
        if c == "(":
            toks.append(_Tok("lparen", c, i))
            i += 1
            continue
        if c == ")":
            toks.append(_Tok("rparen", c, i))
            i += 1
            continue
        if c == ".":
            toks.append(_Tok("dot", c, i))
            i += 1
            continue
        # identifier / keyword
        if c.isalpha() or c == "_":
            j = i + 1
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            word = s[i:j]
            up = word.upper()
            if up in _KEYWORDS:
                toks.append(_Tok("kw", up, i))
            else:
                toks.append(_Tok("ident", word, i))
            i = j
            continue
        raise WhereParseError(f"unexpected character {c!r} at {i}")
    return toks


# ── AST ─────────────────────────────────────────────────────────────────────

class Node:
    def eval(self, get: Callable[[str], Any]) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class Comparison(Node):
    col: str
    op: str           # '=' '<>' '>' '>=' '<' '<='
    val: str
    is_str: bool

    def eval(self, get: Callable[[str], Any]) -> bool:
        raw = get(self.col)
        if raw is None:
            return False
        try:
            if self.is_str:
                rv: Any = str(raw)
                pv: Any = self.val
            else:
                rv = float(raw)
                pv = float(self.val)
        except (TypeError, ValueError):
            return False
        op = self.op
        if op == "=":
            return bool(rv == pv)
        if op == "<>":
            return bool(rv != pv)
        if op == ">":
            return bool(rv > pv)
        if op == ">=":
            return bool(rv >= pv)
        if op == "<":
            return bool(rv < pv)
        if op == "<=":
            return bool(rv <= pv)
        return False


@dataclass
class Not(Node):
    child: Node

    def eval(self, get: Callable[[str], Any]) -> bool:
        return not self.child.eval(get)


@dataclass
class And(Node):
    left: Node
    right: Node

    def eval(self, get: Callable[[str], Any]) -> bool:
        return self.left.eval(get) and self.right.eval(get)


@dataclass
class Or(Node):
    left: Node
    right: Node

    def eval(self, get: Callable[[str], Any]) -> bool:
        return self.left.eval(get) or self.right.eval(get)


# ── Recursive-descent parser ──────────────────────────────────────────────────

class _Parser:
    def __init__(self, toks: list[_Tok]) -> None:
        self.toks = toks
        self.i = 0

    def _peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self) -> _Tok:
        t = self._peek()
        if t is None:
            raise WhereParseError("unexpected end of WHERE expression")
        self.i += 1
        return t

    def _is_kw(self, word: str) -> bool:
        t = self._peek()
        return t is not None and t.kind == "kw" and t.text == word

    def parse(self) -> Node:
        node = self._parse_or()
        trailing = self._peek()
        if trailing is not None:
            raise WhereParseError(f"trailing tokens in WHERE near {trailing.text!r}")
        return node

    def _parse_or(self) -> Node:
        node = self._parse_and()
        while self._is_kw("OR"):
            self._next()
            node = Or(node, self._parse_and())
        return node

    def _parse_and(self) -> Node:
        node = self._parse_not()
        while self._is_kw("AND"):
            self._next()
            node = And(node, self._parse_not())
        return node

    def _parse_not(self) -> Node:
        if self._is_kw("NOT"):
            self._next()
            return Not(self._parse_not())
        return self._parse_atom()

    def _parse_atom(self) -> Node:
        t = self._peek()
        if t is None:
            raise WhereParseError("unexpected end of WHERE expression")
        if t.kind == "lparen":
            self._next()
            node = self._parse_or()
            close = self._peek()
            if close is None or close.kind != "rparen":
                raise WhereParseError("missing closing parenthesis")
            self._next()
            return node
        return self._parse_comparison()

    def _parse_comparison(self) -> Node:
        t = self._peek()
        if t is None:
            raise WhereParseError("expected a comparison")
        # Reject the `value IN propref` form (members lookup) — unsupported here.
        if t.kind in ("num", "str"):
            raise WhereParseError(
                "literal-first predicates (e.g. `N IN x.members`) are not "
                "supported with OR/NOT"
            )
        if t.kind != "ident":
            raise WhereParseError(f"expected a property reference, got {t.text!r}")

        col = self._parse_propref()

        nxt = self._peek()
        if nxt is not None and nxt.kind == "kw" and nxt.text == "IN":
            raise WhereParseError("`IN` / members predicates are not supported with OR/NOT")
        if nxt is None or nxt.kind != "op":
            raise WhereParseError("expected a comparison operator")
        op = self._next().text

        val_tok = self._peek()
        if val_tok is None:
            raise WhereParseError("expected a value after the comparison operator")
        if val_tok.kind == "str":
            self._next()
            return Comparison(col, op, val_tok.text, is_str=True)
        if val_tok.kind == "num":
            self._next()
            return Comparison(col, op, val_tok.text, is_str=False)
        raise WhereParseError(f"expected a string or numeric value, got {val_tok.text!r}")

    def _parse_propref(self) -> str:
        # IDENT ('.' IDENT)?  — the column is the part after the (optional) alias dot.
        first = self._next().text
        nxt = self._peek()
        if nxt is not None and nxt.kind == "dot":
            self._next()
            col_tok = self._peek()
            if col_tok is None or col_tok.kind != "ident":
                raise WhereParseError("expected a column name after '.'")
            self._next()
            return col_tok.text
        return first


def compile_where(text: str) -> Node:
    """Parse a ``WHERE`` clause body into a boolean :class:`Node` tree."""
    toks = _tokenize(text)
    if not toks:
        raise WhereParseError("empty WHERE expression")
    return _Parser(toks).parse()


def evaluate(node: Node, get: Callable[[str], Any]) -> bool:
    """Evaluate *node* against a column accessor ``get(col) -> value | None``."""
    return node.eval(get)


# ── Query rewriting ───────────────────────────────────────────────────────────
#
# Span-finding and OR/NOT detection operate on a *blanked* copy of the query in
# which string-literal contents are replaced by spaces (offsets preserved), so
# keywords/operators inside string values are never matched. The strict
# tokenizer/parser above is then run only on the extracted WHERE body, and only
# when that body actually needs Python handling — keeping non-MATCH and
# MEMBERS-list queries (which contain ``,`` ``[`` ``]``) on the C path untouched.

_MATCH_RE = re.compile(r"^\s*MATCH\b", re.IGNORECASE)
_HE_PATTERN_RE = re.compile(r"\bHYPEREDGE\s*\(", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_TAIL_RE = re.compile(r"\b(?:RETURN|ORDER|LIMIT)\b", re.IGNORECASE)
_NEEDS_PYTHON_RE = re.compile(r"\bOR\b|\bNOT\b|<>|!=", re.IGNORECASE)


def _blank_strings(s: str) -> str:
    """Return a same-length copy of *s* with single/double-quoted regions
    replaced by spaces, so regexes don't match inside string literals."""
    out = list(s)
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in ("'", '"'):
            j = i + 1
            while j < n and s[j] != c:
                out[j] = " "
                j += 1
            out[i] = " "
            if j < n:
                out[j] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def extract_bool_where(query: str) -> tuple[str, Node | None]:
    """
    If *query* is a single-pattern ``MATCH HYPEREDGE`` whose ``WHERE`` needs
    boolean (OR/NOT/``<>``) evaluation, return ``(stripped_query, node)`` where
    ``stripped_query`` has the ``WHERE`` removed (so the C parser performs a full
    scan) and ``node`` is the boolean tree to apply Python-side.

    Otherwise return ``(query, None)`` and the caller uses the normal C path.

    Raises :class:`WhereParseError` if the clause clearly needs Python handling
    but cannot be parsed (so failures are explicit, never silent).
    """
    if not _MATCH_RE.match(query):
        return query, None  # DELETE/UPDATE/CREATE/INSERT/COPY keep the C path.

    blanked = _blank_strings(query)

    # Single pattern only: a join has two `HYPEREDGE (` patterns.
    if len(_HE_PATTERN_RE.findall(blanked)) != 1:
        return query, None

    wm = _WHERE_RE.search(blanked)
    if not wm:
        return query, None
    where_start = wm.start()
    body_start = wm.end()

    tm = _TAIL_RE.search(blanked, body_start)
    if tm:
        where_end = tm.start()
    else:
        stripped = query.rstrip()
        where_end = query.rfind(";") if stripped.endswith(";") else len(query)

    where_text = query[body_start:where_end]
    if not where_text.strip():
        return query, None

    if not _NEEDS_PYTHON_RE.search(_blank_strings(where_text)):
        return query, None  # AND-only with simple ops → let the C parser handle it.

    node = compile_where(where_text)  # may raise WhereParseError

    new_query = (query[:where_start].rstrip() + " " + query[where_end:].lstrip()).strip()
    return new_query, node
