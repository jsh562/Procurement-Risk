"""Package marker for the E007 forecast test tier — see `conftest.py`.

Present because this tier's `test_serialize_properties.py` shares a basename
with `tests/procurement/test_serialize_properties.py`, and pytest's default
import mode distinguishes test modules in non-package directories by basename
alone. Without a marker here the two collide the moment both import cleanly:
whichever is collected second raises `import file mismatch`, and that is a
collection error for the *whole* run rather than a failure in either tier.

This reverses one paragraph of `conftest.py`, which chose unique basenames over
a package marker; that paragraph now records the reversal and its cost. Only
this directory is marked, so `tests/schema` and `tests/procurement` are imported
exactly as they were.
"""

from __future__ import annotations
