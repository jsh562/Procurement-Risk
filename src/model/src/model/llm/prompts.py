"""The extraction prompt, and why the JSON contract lives inside it.

FR-024 / FR-058. One template, resolved per chunk, carrying three things: the
chunk's text, the declared field subset that chunk is being asked about, and the
output contract.

**The contract is prompt text because the gateway has no other place for it.**
`InvocationRequest` declares exactly four fields — `prompt`, `model`,
`trace_id`, `output_schema` — under `extra="forbid"`. There is no temperature,
no system prompt, and no structured-output or tool parameter to put a JSON
schema in, and adding one would be an amendment to E004's closed hashed set
(TR-020). So the shape the model must return is stated in the prompt, and
`output_schema` is what *checks* that it did — the two are not duplicates of one
another: one instructs, the other refuses.

**The attempted field list is rendered into the prompt, and that is what makes a
narrowed vocabulary miss its fixtures.** The gateway's fixture key hashes every
declared request field, the prompt among them (TR-019, TR-020), so a run that
retires a term resolves a different key and — in `replay` — takes FR-056's
run-level failure rather than quietly replaying a fixture recorded for a wider
subset. The output schema is static (see `schemas.py`), so this is the path by
which FR-045's "a changed prompt or schema constraint resolves to a miss" holds
for the subset.

**Field semantics come from the seeded vocabulary, never from the generator's
layout templates.** Each attempted term is described to the model by E003's own
`field_vocabulary.label` and `.description`. A prompt keyed to the per-vendor
labels the corpus generator prints would be reading the answer key by another
route — the thing FR-050's import contract forbids the *baseline* from doing —
and would make the synthetic figures a measurement of the prompt rather than of
extraction.

Nothing here imports `gateway`, and nothing here imports `model.compute`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Final

from model.llm.schemas import DOCUMENT_SCOPE, TRANSMITTAL_FIELD_SUBSET, FieldTerm

__all__ = [
    "PROMPT_TEMPLATE_ID",
    "field_catalogue",
    "prompt_template_digest",
    "render_extraction_prompt",
]

#: Identifies the template shape, for the report and for a human diffing two
#: fixture generations. It is **not** the fixture key's input — the key digests
#: the resolved prompt text (TR-038), so an edit to the wording below moves
#: every key whether or not anybody remembers to bump this string. Kept because
#: "which template produced this" is a question a reader asks of a stored row,
#: and a digest does not answer it.
PROMPT_TEMPLATE_ID: Final[str] = "e006-transmittal-extraction/1"

_INSTRUCTIONS: Final[str] = """\
You are reading one chunk of text taken from a construction submittal
transmittal. Report the fields listed below that are printed in this chunk, and
report nothing else.

Rules, all of them binding:

1. Report a field only if its value is printed in the chunk text below. Do not
   infer a value from context, do not carry one over from what a transmittal
   usually says, and do not complete a partial value. A field that is not
   printed here is simply absent from your answer.
2. Copy `value_text` exactly as printed — same characters, same spelling, same
   punctuation, same case. Do not tidy, expand, normalize, or convert it. A date
   printed `3/14/26` is reported as `3/14/26`.
3. Copy `printed_label` exactly as the label appears beside the value. If the
   value is printed with no label at all, report the nearest heading text that
   introduces it.
4. `field_name` must be one of the names listed under "Fields to report". Never
   invent a name, never abbreviate one, and never report a field that is not on
   the list.
5. `item_ordinal` is the printed item number the value belongs to, counting from
   1 in the order the items appear. Use 0 — and only 0 — for a value the
   document prints once for the whole document rather than against an item.
6. If the same field is printed for several items, report it once per item, each
   with its own `item_ordinal`.
7. If none of the listed fields is printed in this chunk, return an empty
   `values` list. That is a correct answer, not a failure.

Return one JSON object and nothing else — no prose before it, no code fence
around it, no trailing commentary:

{
  "values": [
    {
      "field_name": "<one of the names listed below>",
      "printed_label": "<the label as printed>",
      "value_text": "<the value as printed>",
      "item_ordinal": <integer, 0 or greater>
    }
  ]
}

No other keys are permitted at either level. An object carrying an unlisted key
is rejected.
"""


def field_catalogue(fields: Sequence[FieldTerm]) -> str:
    """The attempted fields, as the prompt shows them.

    One line per term: the name the answer must use, the seeded label, the kind
    the schema stores it as, whether it is a document-scoped or per-item field,
    and the vocabulary's own description. The kind is shown because it tells the
    model *what sort of thing* to look for; it does not ask for a typed value,
    which stays deterministic code's job (FR-049).

    Raises:
        ValueError: `fields` is empty. A catalogue of nothing would produce a
            prompt asking for nothing, and an invocation asking for nothing
            still costs a call and still records a row.
    """
    if not fields:
        raise ValueError(
            "FR-058: the attempted field subset is empty, so the prompt would ask for "
            "nothing while still costing an invocation and a recorded row"
        )
    lines = []
    for entry in fields:
        scope = (
            "printed once for the whole document (item_ordinal 0)"
            if entry.scope == DOCUMENT_SCOPE
            else "printed against a numbered item (item_ordinal 1 or greater)"
        )
        lines.append(
            f"- `{entry.name}` — {entry.label} ({entry.value_kind}; {scope}). {entry.description}"
        )
    return "\n".join(lines)


def render_extraction_prompt(
    *,
    document_id: str,
    page_number: int,
    chunk_ordinal: int,
    chunk_text: str,
    fields: Sequence[FieldTerm],
) -> str:
    """Resolve the template for one chunk.

    Args:
        document_id: the document the chunk belongs to. Present so the resolved
            text — and therefore the fixture key — is per chunk rather than per
            distinct body text; two documents printing an identical chunk are
            two invocations and two ledger entries (FR-069).
        page_number: the page the chunk was read from, one-based.
        chunk_ordinal: the chunk's zero-based ordinal within its document.
        chunk_text: the chunk's body text, verbatim. Never truncated here — a
            chunk is already inside the encoder window by construction, and
            truncating at the prompt would ask the model about text the citation
            says is on the page.
        fields: the declared, unretired subset this chunk is asked about.

    Returns:
        The resolved prompt text, which is both what the provider sees and what
        the fixture key digests.

    Raises:
        ValueError: the chunk carries no text, or the field list is empty. Both
            would produce an invocation that cannot succeed and still records a
            row.
    """
    if not chunk_text.strip():
        raise ValueError(
            f"{document_id} ordinal {chunk_ordinal}: the chunk carries no text to read, so "
            f"the invocation could only ever return an empty answer while costing a "
            f"recorded row"
        )
    if page_number < 1:
        raise ValueError(f"{document_id} ordinal {chunk_ordinal}: page numbers are one-based")
    return (
        f"{_INSTRUCTIONS}\n"
        f"Fields to report:\n\n{field_catalogue(fields)}\n\n"
        f"Chunk under review — document `{document_id}`, page {page_number}, "
        f"chunk ordinal {chunk_ordinal}:\n\n"
        f"---\n{chunk_text}\n---\n"
    )


def prompt_template_digest(fields: Sequence[FieldTerm] = TRANSMITTAL_FIELD_SUBSET) -> str:
    """`ingestion_run.extraction_prompt_digest` — the template, not one prompt.

    Args:
        fields: the declared subset the run attempts. Part of the digest because
            a narrowed subset changes every resolved prompt, and FR-043's input
            tuple has to move when it does; defaulted to the declared subset so
            the ordinary caller states nothing and gets the run's real value.

    Returns:
        `sha256:<64 hex>` over the instruction block and the field catalogue, in
        the form `ck_ingestion_run__extraction_prompt_digest_format` admits.

    **Over the template and its catalogue, never over a resolved prompt.** A
    resolved prompt carries one chunk's own text, so digesting one would give a
    per-chunk value where the run record needs a per-run one — and FR-043's
    input tuple would then move for every document, reloading all 51 whenever
    any one of them changed.

    **This is not the fixture key** and does not try to be. The key digests the
    *resolved* request (TR-038), which is what makes a changed prompt resolve to
    a miss; this is the run record's account of which template produced the
    run's invocations, and it moves for exactly the same edits.
    """
    payload = f"{PROMPT_TEMPLATE_ID}\n{_INSTRUCTIONS}\n{field_catalogue(fields)}"
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
