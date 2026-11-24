import random

import pytest

from timesheet import Sheet, parse
from timesheet.formula import expand_range, index_to_col, col_to_index


def sheet_with(**cells):
    s = Sheet()
    for k, v in cells.items():
        s.set(k, v)
    return s


@pytest.mark.parametrize("formula,expected", [
    ("1+2*3", 7.0),
    ("(1+2)*3", 9.0),
    ("2^3^2", 512.0),        # right associative
    ("-2^2", 4.0),           # Excel: unary minus binds tighter than ^
    ("10-4-3", 3.0),
    ("50%", 0.5),
    ('"a"&"b"&1', "ab1"),
    ("1<2", True),
    ('"abc"="ABC"', True),
    ("1/0", "#DIV/0!"),
    ("SUM(1,2,3)", 6.0),
    ("IF(1>2,\"yes\",\"no\")", "no"),
    ("ROUND(2.567,1)", 2.6),
    ("NOPE(1)", "#NAME?"),
])
def test_constant_formulas(formula, expected):
    s = sheet_with(A1="=" + formula)
    assert s.get("A1") == expected


def test_references_and_ranges():
    s = sheet_with(A1="1", A2="2", A3="hello", A4="4", B1="=SUM(A1:A4)", B2="=AVERAGE(A1:A4)",
                   B3="=COUNT(A1:A4)", B4="=$A$1+a2")
    assert s.get("B1") == 7.0          # text in a range is skipped
    assert s.get("B2") == pytest.approx(7 / 3)
    assert s.get("B3") == 3.0
    assert s.get("B4") == 3.0
    assert s.get("A3") + "x" == "hellox"


def test_recalculation_chain():
    s = sheet_with(A1="1", B1="=A1*2", C1="=B1+A1")
    assert s.get("C1") == 3.0
    s.set("A1", "10")
    assert (s.get("B1"), s.get("C1")) == (20.0, 30.0)
    s.set("B1", "=A1*3")
    assert s.get("C1") == 40.0


def test_cycles():
    s = sheet_with(A1="=B1", B1="=A1", C1="=A1+1", D1="5")
    assert s.get("A1") == "#CYCLE!"
    assert s.get("C1") == "#CYCLE!"
    s.set("B1", "=D1")  # break the cycle
    assert (s.get("A1"), s.get("C1")) == (5.0, 6.0)


def test_errors_propagate():
    s = sheet_with(A1="=1/0", B1="=A1+1", C1="=SUM(A1,1)")
    assert s.get("B1") == "#DIV/0!" and s.get("C1") == "#DIV/0!"


def test_column_names():
    for n in (1, 26, 27, 52, 702, 703, 16384):
        assert col_to_index(index_to_col(n)) == n
    assert index_to_col(16384) == "XFD"
    assert expand_range("B2", "A1") == ["A1", "B1", "A2", "B2"]


def test_parse_errors_become_cell_errors():
    s = sheet_with(A1="=1+")
    assert s.get("A1") == "#ERROR!"
    with pytest.raises(ValueError):
        parse("SUM(1,")


def test_history_replay_matches_live_sheet():
    rng = random.Random(7)
    s = Sheet()
    cells = [f"{c}{r}" for c in "ABC" for r in range(1, 5)]
    snapshots = []
    for _ in range(300):
        cell = rng.choice(cells)
        if rng.random() < 0.5:
            s.set(cell, str(rng.randint(-5, 5)))
        else:
            a, b = rng.sample(cells, 2)
            s.set(cell, f"={a}+{b}*2")
        snapshots.append({c: s.get(c) for c in cells})
    for n in (1, 50, 150, 300):
        replay = s.state_at(n)
        assert {c: replay.get(c) for c in cells} == snapshots[n - 1]
    for _ in range(100):
        s.undo()
    assert {c: s.get(c) for c in cells} == snapshots[199]
