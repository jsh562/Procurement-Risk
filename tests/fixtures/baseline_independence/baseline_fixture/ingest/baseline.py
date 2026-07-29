"""The violation. Stands in for `model.ingest.baseline`.

Two reaches, both forbidden, and the difference between them is the whole
argument for `allow_indirect_imports = False`:

`corpus.model` is imported **directly** — the crude version, where the baseline
reads the pre-render document model and reports the values it was handed. Any
forbidden contract catches this, indirect detection or not.

`corpus.generator` is imported **indirectly**. It is an ordinary corpus module
that violates nothing itself; it merely reads the templates and the renderer,
because generating a corpus is what it does. The baseline importing *it* leaves
no direct edge to `templates` or `render` at all, so with indirect detection off
this reads as a clean module. It is the shape the evasion actually takes, and it
is why the real contract turns indirect detection on rather than accepting the
cheaper check.

A baseline that reads any of these is not extracting from a document, it is
reading the answer key — and an opponent that cannot lose makes every quality
figure published beside it flattery rather than evidence (Principle VIII).
"""

from baseline_fixture.corpus import generator, model

__all__ = ["generator", "model"]
