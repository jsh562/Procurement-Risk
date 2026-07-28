"""Stands in for `model.corpus.render`: document model in, PDF out.

Part of the answer key. It holds the field-to-position mapping the renderer
used, which is the layout a real extractor has to recover from pixels.
"""


def render(document: dict[str, str]) -> bytes:
    return repr(document).encode("utf-8")
