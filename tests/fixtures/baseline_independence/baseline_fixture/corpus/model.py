"""Stands in for `model.corpus.model`: the pre-render document model.

The answer key itself — every field value, before rendering lost any of them.
"""


class Document:
    def __init__(self, manufacturer: str, part_number: str) -> None:
        self.manufacturer = manufacturer
        self.part_number = part_number
