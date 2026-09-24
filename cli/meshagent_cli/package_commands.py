"""Conservative package-install parsing for editor hooks.

The parser never executes or expands shell input. It recognizes a finite set of
package-manager command grammars and inspects only that simple-command segment,
so operands belonging to ``cd``, ``head``, ``tail``, ``git show`` and similar
commands cannot become package evidence merely because another segment installs
something.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class InstallTarget:
    name: str
    version: str
    ecosystem: str
    manager: str
    exact: bool


_PYPI_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_NPM_NAME = re.compile(r"^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*$")
_EXACT_VERSION = re.compile(r"^[0-9][A-Za-z0-9._+-]*$")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")

# Values consumed by options are configuration, never package operands.
_OPTIONS_WITH_VALUE = {
    "-c", "--constraint", "-e", "--editable", "-f", "--find-links",
    "-i", "--index-url", "--extra-index-url", "--proxy", "--retries",
    "--timeout", "--trusted-host", "--target", "--platform", "--python-version",
    "--implementation", "--abi", "--root", "--prefix", "--cache-dir",
    "--registry", "--tag", "--workspace", "--filter", "--cwd", "--directory",
    "--source", "--group", "--python", "--extras",
}
_INDIRECT_OPTIONS = {"-r", "--requirement"}


def _segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return []
    out: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token and all(char in ";&|<>()" for char in token):
            if current:
                out.append(current)
                current = []
            continue
        current.append(token)
    if current:
        out.append(current)
    return out


def _unwrap(tokens: list[str]) -> list[str]:
    found = list(tokens)
    while found:
        executable = os.path.basename(found[0])
        if executable == "command":
            found.pop(0)
            continue
        if executable == "env":
            found.pop(0)
            while found and (_ASSIGNMENT.match(found[0]) or found[0].startswith("-")):
                found.pop(0)
            continue
        if executable == "sudo":
            found.pop(0)
            while found and found[0].startswith("-"):
                option = found.pop(0)
                if option in {"-u", "-g", "-h", "-p", "-C", "-T", "-R"} and found:
                    found.pop(0)
            continue
        while found and _ASSIGNMENT.match(found[0]):
            found.pop(0)
        break
    return found


def _grammar(tokens: list[str]) -> tuple[str, str, str, list[str]] | None:
    tokens = _unwrap(tokens)
    if not tokens:
        return None
    executable = os.path.basename(tokens[0])
    rest = tokens[1:]
    py = executable == "python" or executable == "python3" or bool(re.match(r"^python3(?:\.\d+)?$", executable))
    if py and len(rest) >= 3 and rest[0:2] == ["-m", "pip"] and rest[2] == "install":
        return "pip", "PyPI", "install", rest[3:]
    if executable in {"pip", "pip3"} and rest[:1] == ["install"]:
        return "pip", "PyPI", "install", rest[1:]
    if executable == "uv" and rest[:2] == ["pip", "install"]:
        return "uv", "PyPI", "install", rest[2:]
    if executable == "uv" and rest[:1] == ["add"]:
        return "uv", "PyPI", "add", rest[1:]
    if executable == "poetry" and rest[:1] == ["add"]:
        return "poetry", "PyPI", "add", rest[1:]
    if executable == "npm" and rest[:1] and rest[0] in {"install", "i"}:
        return "npm", "npm", rest[0], rest[1:]
    if executable == "yarn" and rest[:1] == ["add"]:
        return "yarn", "npm", "add", rest[1:]
    return None


def _operands(tokens: list[str]) -> list[str]:
    operands: list[str] = []
    skip = False
    literal = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token == "--":
            literal = True
            continue
        if not literal and token in _INDIRECT_OPTIONS:
            skip = True
            continue
        if not literal and token in _OPTIONS_WITH_VALUE:
            skip = True
            continue
        if not literal and token.startswith("--") and "=" in token:
            continue
        if not literal and token.startswith("-"):
            continue
        operands.append(token)
    return operands


def _pypi(spec: str) -> tuple[str, str, bool] | None:
    if any(mark in spec for mark in ("/", "\\", "://", "${", "$(", "`")):
        return None
    raw = spec.split(";", 1)[0].strip()
    for separator in ("===", "=="):
        if separator in raw:
            name, version = raw.split(separator, 1)
            name = name.split("[", 1)[0]
            if _PYPI_NAME.match(name) and _EXACT_VERSION.match(version):
                return name, version, True
            return None
    name = re.split(r"[<>=!~]", raw, maxsplit=1)[0].split("[", 1)[0]
    if _PYPI_NAME.match(name):
        return name, "", False
    return None


def _poetry(spec: str) -> tuple[str, str, bool] | None:
    if "@" in spec:
        name, version = spec.rsplit("@", 1)
        if _PYPI_NAME.match(name) and _EXACT_VERSION.match(version):
            return name, version, True
        if _PYPI_NAME.match(name):
            return name, "", False
        return None
    return _pypi(spec)


def _npm(spec: str) -> tuple[str, str, bool] | None:
    if any(mark in spec for mark in ("\\", "://", "${", "$(", "`")):
        return None
    name, version = spec, ""
    if spec.startswith("@"):
        split = spec.rfind("@")
        if split > spec.find("/"):
            name, version = spec[:split], spec[split + 1:]
    elif "@" in spec:
        name, version = spec.rsplit("@", 1)
    if not _NPM_NAME.match(name):
        return None
    exact = bool(version and _EXACT_VERSION.match(version))
    return name, version if exact else "", exact


def parse_installs(command: str) -> list[InstallTarget]:
    """Return direct package targets from recognized install command segments."""
    found: list[InstallTarget] = []
    seen: set[tuple[str, str, str, str]] = set()
    for segment in _segments(command):
        grammar = _grammar(segment)
        if grammar is None:
            continue
        manager, ecosystem, _action, rest = grammar
        for operand in _operands(rest):
            parsed = (
                _npm(operand) if ecosystem == "npm"
                else _poetry(operand) if manager == "poetry"
                else _pypi(operand)
            )
            if parsed is None:
                continue
            name, version, exact = parsed
            key = (ecosystem, name.lower(), version, manager)
            if key in seen:
                continue
            seen.add(key)
            found.append(InstallTarget(name, version, ecosystem, manager, exact))
    return found


def pinned_installs(command: str) -> list[InstallTarget]:
    return [target for target in parse_installs(command) if target.exact]
