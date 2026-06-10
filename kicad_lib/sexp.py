"""Lossless s-expression parser/serializer for KiCad files.

Representation:
  node  -> list, first element usually a Sym tag
  Sym   -> unquoted token, raw text preserved (numbers stay as written)
  QStr  -> quoted string; raw escaped form preserved for byte-stable
           round-trips, .value decodes on demand

Tolerant of unknown tokens by construction: anything it doesn't
understand is still parsed, carried, and re-emitted verbatim.
"""

from __future__ import annotations

from typing import Iterator, Union

SExpr = Union["Sym", "QStr", list]


class Sym(str):
    """Unquoted token. Subclasses str; raw text is the value."""

    __slots__ = ()


class QStr(str):
    """Quoted string. The str content is the RAW (escaped) text between
    the quotes; use .value for the decoded form."""

    __slots__ = ()

    _UNESCAPE = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r"}
    _ESCAPE = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}

    @property
    def value(self) -> str:
        if "\\" not in self:
            return str(self)
        out, i = [], 0
        while i < len(self):
            c = self[i]
            if c == "\\" and i + 1 < len(self):
                out.append(self._UNESCAPE.get(self[i + 1], "\\" + self[i + 1]))
                i += 2
            else:
                out.append(c)
                i += 1
        return "".join(out)

    @classmethod
    def of(cls, value: str) -> "QStr":
        """Build from a decoded value, escaping as KiCad does."""
        return cls("".join(cls._ESCAPE.get(c, c) for c in value))


class ParseError(ValueError):
    pass


def parse(text: str) -> list:
    """Parse one top-level s-expression; raises on imbalance/trailing junk."""
    items, pos = _parse_many(text, 0)
    if len(items) != 1 or not isinstance(items[0], list):
        raise ParseError(f"expected exactly one top-level list, got {len(items)}")
    return items[0]


def _parse_many(text: str, pos: int) -> tuple[list, int]:
    n = len(text)
    root: list = []
    stack: list[list] = [root]
    while pos < n:
        c = text[pos]
        if c.isspace():
            pos += 1
        elif c == "(":
            node: list = []
            stack[-1].append(node)
            stack.append(node)
            pos += 1
        elif c == ")":
            if len(stack) == 1:
                raise ParseError(f"unbalanced ')' at offset {pos}")
            stack.pop()
            pos += 1
        elif c == '"':
            end = pos + 1
            while end < n:
                if text[end] == "\\":
                    end += 2
                elif text[end] == '"':
                    break
                else:
                    end += 1
            if end >= n:
                raise ParseError(f"unterminated string at offset {pos}")
            stack[-1].append(QStr(text[pos + 1 : end]))
            pos = end + 1
        else:
            end = pos
            while end < n and not text[end].isspace() and text[end] not in '()"':
                end += 1
            stack[-1].append(Sym(text[pos:end]))
            pos = end
    if len(stack) != 1:
        raise ParseError(f"unbalanced '(' — {len(stack) - 1} unclosed")
    return root, pos


def dumps(node: SExpr, indent: int = 0) -> str:
    """Serialize in KiCad 10 style: tab indent, atoms-only nodes inline,
    any node with a list child goes multiline."""
    return "".join(_emit(node, indent)) + "\n"


def _atom_text(a: SExpr) -> str:
    if isinstance(a, QStr):
        return f'"{a}"'
    return str(a)


def _emit(node: SExpr, indent: int) -> Iterator[str]:
    tab = "\t" * indent
    if not isinstance(node, list):
        yield tab + _atom_text(node)
        return
    if all(not isinstance(x, list) for x in node):
        yield tab + "(" + " ".join(_atom_text(x) for x in node) + ")"
        return
    # eeschema groups all (xy ...) points of a pts block on ONE line
    if tag_of(node) == "pts" and all(
        isinstance(x, list) and tag_of(x) == "xy"
        and not any(isinstance(y, list) for y in x)
        for x in node[1:]
    ):
        row = " ".join(
            "(" + " ".join(_atom_text(y) for y in x) + ")" for x in node[1:]
        )
        yield tab + "(pts\n" + tab + "\t" + row + "\n" + tab + ")"
        return
    head = [x for x in node if not isinstance(x, list)]
    yield tab + "(" + " ".join(_atom_text(x) for x in head)
    for x in node:
        if isinstance(x, list):
            yield "\n"
            yield from _emit(x, indent + 1)
    yield "\n" + tab + ")"


def load_file(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def save_file(path: str, root: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(root))


# ---------------------------------------------------------------- queries

def tag_of(node: SExpr) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], Sym):
        return str(node[0])
    return None


def find_all(node: list, tag: str) -> Iterator[list]:
    for x in node:
        if isinstance(x, list) and tag_of(x) == tag:
            yield x


def find(node: list, tag: str) -> list | None:
    return next(find_all(node, tag), None)


def walk(node: list, tag: str) -> Iterator[list]:
    """Depth-first search for tag at any depth."""
    for x in node:
        if isinstance(x, list):
            if tag_of(x) == tag:
                yield x
            yield from walk(x, tag)


def atoms(node: list) -> list[str]:
    """Positional atom values (decoded) of a node, tag excluded."""
    return [
        x.value if isinstance(x, QStr) else str(x)
        for x in node[1:]
        if not isinstance(x, list)
    ]


def tokens(node: SExpr) -> Iterator[str]:
    """Flat token stream — the basis of round-trip equality checks."""
    if isinstance(node, list):
        yield "("
        for x in node:
            yield from tokens(x)
        yield ")"
    else:
        yield _atom_text(node)
