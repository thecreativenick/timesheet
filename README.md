# timesheet

A spreadsheet engine with a time machine: drag a slider back through every edit, branch a
what-if copy, and merge it back.

## Status

Early scaffold. Done so far:

- Formula tokenizer and Pratt parser: numbers, strings, booleans, `A1`/`$A$1` references,
  ranges, `+ - * / ^ & %`, comparisons, with Excel's precedence (e.g. `-2^2 = 4`).
- Evaluator with Excel-style error values (`#DIV/0!`, `#NAME?`, `#CYCLE!`, ...) and a first
  10 functions (SUM, AVERAGE, MIN, MAX, COUNT, ABS, ROUND, SQRT, CONCAT, IF).
- A sheet with a dependency graph, lazy recalculation and cycle detection.
- An edit log with `state_at(n)` replay and undo, checked against live states in a random test.

## Planned milestones

1. About 300 functions (text, date, lookup, statistical, financial), lazy IF/IFERROR, array results.
2. `.xlsx` import/export. Test workbooks are generated, and LibreOffice (headless) computes
   the reference cached values.
3. Fast history: periodic snapshots plus log replay, so any past state loads in under 200 ms.
4. Branches and three-way merge, with conflict rules for moved and rewritten cells.
5. A localhost grid UI with a history slider and a branch/merge view.

## Success bar

Formulas match LibreOffice's cached values in 500 generated workbooks, jumping to any of
100,000 past states of a million-cell sheet takes under 200 ms, and 1,000 generated merges
match a replay oracle.

## Development

```bash
pip install -e .[dev]
pytest
```
