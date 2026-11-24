"""A sheet: cell contents, a dependency graph with lazy recalculation, and an edit log.

The edit log is the start of the "time machine": every change is an entry,
and state_at(n) rebuilds the sheet as it was after n edits. Snapshots and
branches with three-way merge come next.
"""

from __future__ import annotations

from dataclasses import dataclass

from .formula import CYCLE, ParseError, XLError, evaluate, normalize, parse, references


@dataclass(frozen=True)
class Edit:
    cell: str
    old: str | None
    new: str | None


def parse_literal(raw: str):
    if raw == "":
        return None
    upper = raw.upper()
    if upper in ("TRUE", "FALSE"):
        return upper == "TRUE"
    try:
        return float(raw)
    except ValueError:
        return raw


class Sheet:
    def __init__(self):
        self.raw: dict[str, str] = {}
        self.ast: dict[str, object] = {}
        self.deps: dict[str, set[str]] = {}      # cell -> cells it reads
        self.rdeps: dict[str, set[str]] = {}     # cell -> cells that read it
        self.cache: dict[str, object] = {}
        self.log: list[Edit] = []
        self._evaluating: set[str] = set()

    # ---------------------------------------------------------------- edits
    def set(self, cell: str, raw: str | None, record: bool = True) -> None:
        cell = normalize(cell)
        old = self.raw.get(cell)
        if record:
            self.log.append(Edit(cell, old, raw))
        for d in self.deps.pop(cell, set()):
            self.rdeps.get(d, set()).discard(cell)
        self.ast.pop(cell, None)
        if raw is None or raw == "":
            self.raw.pop(cell, None)
        else:
            self.raw[cell] = raw
            if raw.startswith("="):
                try:
                    tree = parse(raw[1:])
                except ParseError:
                    tree = ("error", XLError("#ERROR!"))
                self.ast[cell] = tree
                refs = references(tree) if tree[0] != "error" else set()
                self.deps[cell] = refs
                for d in refs:
                    self.rdeps.setdefault(d, set()).add(cell)
        self._invalidate(cell)

    def _invalidate(self, cell: str) -> None:
        stack, seen = [cell], set()
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            self.cache.pop(c, None)
            stack.extend(self.rdeps.get(c, ()))

    # --------------------------------------------------------------- values
    def get(self, cell: str):
        cell = normalize(cell)
        if cell in self.cache:
            return self.cache[cell]
        if cell in self._evaluating:
            return CYCLE
        if cell in self.ast:
            tree = self.ast[cell]
            if tree[0] == "error":
                value = tree[1]
            else:
                self._evaluating.add(cell)
                try:
                    value = evaluate(tree, self.get)
                finally:
                    self._evaluating.discard(cell)
                if isinstance(value, list):
                    value = value[0] if value else None  # implicit intersection, simplified
        else:
            value = parse_literal(self.raw.get(cell, ""))
        self.cache[cell] = value
        return value

    # -------------------------------------------------------------- history
    def state_at(self, n: int) -> "Sheet":
        """A fresh sheet holding the state after the first n edits."""
        s = Sheet()
        for e in self.log[:n]:
            s.set(e.cell, e.new)
        return s

    def undo(self) -> None:
        if self.log:
            e = self.log.pop()
            self.set(e.cell, e.old, record=False)
