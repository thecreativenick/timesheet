"""Formula tokenizer, Pratt parser and evaluator.

AST nodes are tuples:
    ("num", float) ("str", s) ("bool", b) ("ref", "A1") ("range", "A1", "B3")
    ("unary", op, node) ("binary", op, left, right) ("call", NAME, [args])
"""

from __future__ import annotations

import math
import re
from typing import Callable

TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
      | (?P<str>"(?:[^"]|"")*")
      | (?P<ref>\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)(?![A-Za-z0-9_(])
      | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
      | (?P<op><=|>=|<>|[-+*/^&=<>(),%])
    )""",
    re.VERBOSE,
)
CELL_RE = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d+)$")


class XLError(str):
    """An Excel-style error value (#DIV/0!, #VALUE!, ...). Propagates through formulas."""


DIV0, VALUE, NAME, REF, CYCLE, NA = (XLError(s) for s in
                                     ("#DIV/0!", "#VALUE!", "#NAME?", "#REF!", "#CYCLE!", "#N/A"))


class ParseError(ValueError):
    pass


# ---------------------------------------------------------------- addresses
def col_to_index(col: str) -> int:
    n = 0
    for ch in col.upper():
        n = n * 26 + ord(ch) - 64
    return n


def index_to_col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def normalize(ref: str) -> str:
    m = CELL_RE.match(ref)
    if not m:
        raise ParseError(f"bad cell reference {ref!r}")
    return m.group(1).upper() + m.group(2)


def expand_range(a: str, b: str) -> list[str]:
    ma, mb = CELL_RE.match(a), CELL_RE.match(b)
    c1, c2 = sorted((col_to_index(ma.group(1)), col_to_index(mb.group(1))))
    r1, r2 = sorted((int(ma.group(2)), int(mb.group(2))))
    return [f"{index_to_col(c)}{r}" for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]


# ------------------------------------------------------------------- parse
def tokenize(src: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    src = src.rstrip()
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m or m.end() == pos:
            raise ParseError(f"unexpected character at {pos}: {src[pos:]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


BINARY = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1, "&": 2, "+": 3, "-": 3,
          "*": 4, "/": 4, "^": 6}
PREFIX_BP = 7  # Excel binds unary minus tighter than ^, so =-2^2 is 4


def parse(src: str):
    toks = tokenize(src)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else (None, None)

    def take(expected: str | None = None):
        nonlocal pos
        tok = peek()
        if tok[0] is None or (expected is not None and tok[1] != expected):
            raise ParseError(f"expected {expected or 'token'}, got {tok[1]!r}")
        pos += 1
        return tok

    def expr(min_bp: int):
        kind, val = take()
        if kind == "num":
            left = ("num", float(val))
        elif kind == "str":
            left = ("str", val[1:-1].replace('""', '"'))
        elif kind == "ref":
            if ":" in val:
                a, b = val.split(":")
                left = ("range", normalize(a), normalize(b))
            else:
                left = ("ref", normalize(val))
        elif kind == "name":
            name = val.upper()
            if peek()[1] == "(":
                take("(")
                args = []
                if peek()[1] != ")":
                    args.append(expr(0))
                    while peek()[1] == ",":
                        take(",")
                        args.append(expr(0))
                take(")")
                left = ("call", name, args)
            elif name in ("TRUE", "FALSE"):
                left = ("bool", name == "TRUE")
            else:
                raise ParseError(f"unknown name {val!r}")
        elif val == "(":
            left = expr(0)
            take(")")
        elif val in ("-", "+"):
            left = ("unary", val, expr(PREFIX_BP))
        else:
            raise ParseError(f"unexpected {val!r}")

        while True:
            kind, op = peek()
            if op == "%":
                take()
                left = ("binary", "/", left, ("num", 100.0))
                continue
            if kind != "op" or op not in BINARY:
                break
            bp = BINARY[op]
            if bp < min_bp:
                break
            take()
            right = expr(bp if op == "^" else bp + 1)
            left = ("binary", op, left, right)
        return left

    tree = expr(0)
    if pos != len(toks):
        raise ParseError(f"unexpected trailing {toks[pos][1]!r}")
    return tree


def references(node) -> set[str]:
    """Every cell a formula reads (ranges expanded)."""
    kind = node[0]
    if kind == "ref":
        return {node[1]}
    if kind == "range":
        return set(expand_range(node[1], node[2]))
    if kind == "unary":
        return references(node[2])
    if kind == "binary":
        return references(node[2]) | references(node[3])
    if kind == "call":
        return set().union(*(references(a) for a in node[2])) if node[2] else set()
    return set()


# ---------------------------------------------------------------- evaluate
def to_number(v):
    if isinstance(v, XLError):
        return v
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except ValueError:
        return VALUE


def to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def flatten(values):
    for v in values:
        if isinstance(v, list):
            yield from v
        else:
            yield v


def _numbers(args):
    """Numbers among args; text and blanks inside ranges are skipped, as in Excel."""
    out = []
    for a in args:
        if isinstance(a, list):
            for v in a:
                if isinstance(v, XLError):
                    return v
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append(float(v))
        else:
            n = to_number(a)
            if isinstance(n, XLError):
                return n
            out.append(n)
    return out


def _agg(fn):
    def run(args):
        nums = _numbers(args)
        return nums if isinstance(nums, XLError) else fn(nums)
    return run


def _if(args):
    if len(args) not in (2, 3):
        return VALUE
    cond = args[0]
    if isinstance(cond, XLError):
        return cond
    truthy = bool(to_number(cond)) if not isinstance(cond, str) else VALUE
    if isinstance(truthy, XLError):
        return truthy
    return args[1] if truthy else (args[2] if len(args) == 3 else False)


FUNCTIONS: dict[str, Callable] = {
    "SUM": _agg(sum),
    "AVERAGE": _agg(lambda xs: sum(xs) / len(xs) if xs else DIV0),
    "MIN": _agg(lambda xs: min(xs) if xs else 0.0),
    "MAX": _agg(lambda xs: max(xs) if xs else 0.0),
    "COUNT": _agg(lambda xs: float(len(xs))),
    "ABS": _agg(lambda xs: abs(xs[0]) if len(xs) == 1 else VALUE),
    "ROUND": _agg(lambda xs: float(round(xs[0], int(xs[1]))) if len(xs) == 2 else VALUE),
    "SQRT": _agg(lambda xs: (math.sqrt(xs[0]) if xs[0] >= 0 else XLError("#NUM!")) if len(xs) == 1 else VALUE),
    "CONCAT": lambda args: next((v for v in flatten(args) if isinstance(v, XLError)), None)
    or "".join(to_text(v) for v in flatten(args)),
    "IF": _if,
}


def _compare(op, a, b):
    # Excel ordering: numbers < text < booleans; text compares case-insensitively.
    def rank(v):
        if isinstance(v, bool):
            return (2, v)
        if isinstance(v, str):
            return (1, v.lower())
        return (0, 0.0 if v is None else float(v))
    ra, rb = rank(a), rank(b)
    return {"=": ra == rb, "<>": ra != rb, "<": ra < rb, ">": ra > rb,
            "<=": ra <= rb, ">=": ra >= rb}[op]


def evaluate(node, get: Callable[[str], object]):
    kind = node[0]
    if kind in ("num", "str", "bool"):
        return node[1]
    if kind == "ref":
        return get(node[1])
    if kind == "range":
        return [get(c) for c in expand_range(node[1], node[2])]
    if kind == "unary":
        v = to_number(evaluate(node[2], get))
        return v if isinstance(v, XLError) else (-v if node[1] == "-" else v)
    if kind == "binary":
        op = node[1]
        a, b = evaluate(node[2], get), evaluate(node[3], get)
        for v in (a, b):
            if isinstance(v, XLError):
                return v
        if op == "&":
            return to_text(a) + to_text(b)
        if op in ("=", "<>", "<", ">", "<=", ">="):
            return _compare(op, a, b)
        x, y = to_number(a), to_number(b)
        for v in (x, y):
            if isinstance(v, XLError):
                return v
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            return DIV0 if y == 0 else x / y
        if op == "^":
            try:
                return float(x ** y)
            except (OverflowError, ZeroDivisionError):
                return XLError("#NUM!")
    if kind == "call":
        fn = FUNCTIONS.get(node[1])
        if fn is None:
            return NAME
        return fn([evaluate(a, get) for a in node[2]])
    raise ValueError(f"bad node {node!r}")
