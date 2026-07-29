"""Package marker, and it is load-bearing rather than conventional.

`plan.md` §Project Structure names this suite `src/model/tests/llm/
test_extraction.py`, and E003 already owns `src/model/tests/schema/
test_extraction.py`. Under pytest's default `prepend` import mode a test file's
module name is its path relative to the first ancestor directory *without* an
`__init__.py`, so two files sharing a basename in two package-less directories
resolve to one module name — and collection fails with an import-file mismatch
naming whichever was collected first.

This file makes the directory a package, so this suite imports as
`llm.test_extraction` while E003's stays `test_extraction`. The alternatives
were to rename a file the plan names, or to switch the whole entry to
`--import-mode=importlib`, which changes how every existing suite is imported to
fix a collision between two.
"""
