"""FR-023a / FR-031a: the approximated code set and the committed field labels.

Two vocabularies, one of them committed, and both are preconditions of rules
that run later. The descriptor and action codes live in code because no
committed artifact carries them; the field labels live in
`field-label-vocabulary.json` because the injector that mis-labels a field and
the deriver that recovers the mis-labelling must read one file.

**Why the loader's refusals are exercised here.** Every check in
`load_vocabulary` exists so that a later rule is decidable. Alternates disjoint
from *every* canonical label is what makes VR-035b answerable at all: a token
that is one field's alternate and another's canonical label identifies no field,
so a deriver could not say whether the document carried
`INCONSISTENT_FIELD_LABEL` or was correctly labelled for a different field. A
refusal that has never been observed is a precondition nobody can rely on.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from model.corpus.codes import (
    ACTION_CODES,
    DESCRIPTOR_CODES,
    STRUCTURAL_FIELD_KEYS,
    VOCABULARY,
    CodesError,
    action_code,
    alternate_labels,
    canonical_label,
    descriptor_code,
    load_vocabulary,
)

DATE_KEYS = ("date_submitted", "date_received", "date_returned")


def _valid() -> dict[str, Any]:
    """A minimal vocabulary that satisfies every precondition, to mutate from."""
    keys = (*STRUCTURAL_FIELD_KEYS, *DATE_KEYS)
    return {
        "fields": {
            key: {
                "canonical_label": f"Canonical {index}",
                "alternate_labels": [f"Alternate {index}A", f"Alternate {index}B"],
            }
            for index, key in enumerate(keys)
        },
        "structural_fields": list(STRUCTURAL_FIELD_KEYS),
        "date_field_order": list(DATE_KEYS),
    }


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _mutated(**changes: Any) -> dict[str, Any]:
    document = _valid()
    document.update(deepcopy(changes))
    return document


# --- the approximated code set ----------------------------------------------


def test_the_action_codes_admit_both_a_closing_and_a_non_closing_outcome() -> None:
    """FR-025's differing action code is unconstructible without both."""
    assert any(code.closes_chain for code in ACTION_CODES)
    assert any(not code.closes_chain for code in ACTION_CODES)


def test_a_descriptor_and_an_action_resolve_by_their_recorded_token() -> None:
    assert descriptor_code(DESCRIPTOR_CODES[0].code) is DESCRIPTOR_CODES[0]
    assert action_code(ACTION_CODES[0].letter) is ACTION_CODES[0]


@pytest.mark.parametrize("token", ["SD-99", "", None])
def test_a_token_outside_the_approximated_set_is_refused(token: object) -> None:
    with pytest.raises(CodesError):
        descriptor_code(token)  # type: ignore[arg-type]
    with pytest.raises(CodesError):
        action_code(token)  # type: ignore[arg-type]


# --- the committed vocabulary ------------------------------------------------


def test_every_structural_field_has_a_canonical_and_an_alternate_label() -> None:
    for key in STRUCTURAL_FIELD_KEYS:
        assert canonical_label(key).strip()
        assert alternate_labels(key), key


def test_a_field_outside_the_committed_vocabulary_is_refused() -> None:
    with pytest.raises(CodesError):
        VOCABULARY.labels("a_field_the_vocabulary_does_not_hold")


def test_the_committed_structural_fields_are_exactly_the_six() -> None:
    """A restatement that is compared is a cross-check; one merely repeated is
    drift, which is why the committed list is held against FR-023's six."""
    assert VOCABULARY.structural_field_keys == STRUCTURAL_FIELD_KEYS
    assert set(VOCABULARY.field_keys) >= set(STRUCTURAL_FIELD_KEYS)


# --- the loader's refusals ---------------------------------------------------


def test_load_vocabulary_reads_an_explicit_path(tmp_path: Path) -> None:
    loaded = load_vocabulary(_write(tmp_path / "vocab.json", _valid()))
    assert loaded.structural_field_keys == STRUCTURAL_FIELD_KEYS
    assert loaded.date_field_order == DATE_KEYS


def test_load_vocabulary_refuses_an_unresolvable_root(tmp_path: Path) -> None:
    with pytest.raises(CodesError):
        load_vocabulary(root=tmp_path / "no-such-corpus-root")


def test_load_vocabulary_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CodesError):
        load_vocabulary(tmp_path / "absent.json")


def test_load_vocabulary_refuses_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "vocab.json"
    path.write_bytes(b"\xff\xfe not json\n")
    with pytest.raises(CodesError):
        load_vocabulary(path)


def _with_field(key: str, record: object) -> dict[str, Any]:
    document = _valid()
    document["fields"][key] = record
    return document


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="not-an-object"),
        pytest.param(_mutated(extra=1), id="unexpected-top-level-key"),
        pytest.param(_mutated(fields={}), id="fields-empty"),
        pytest.param(_mutated(fields=[]), id="fields-not-an-object"),
        pytest.param(_with_field("action_stamp", []), id="field-record-not-an-object"),
        pytest.param(
            _with_field(
                "action_stamp",
                {"canonical_label": "A", "alternate_labels": ["B"], "x": 1},
            ),
            id="field-record-unexpected-key",
        ),
        pytest.param(
            _with_field("action_stamp", {"canonical_label": 7, "alternate_labels": ["B"]}),
            id="canonical-label-not-a-string",
        ),
        pytest.param(
            _with_field("action_stamp", {"canonical_label": "  ", "alternate_labels": ["B"]}),
            id="canonical-label-blank",
        ),
        pytest.param(
            _with_field("action_stamp", {"canonical_label": "A", "alternate_labels": "B"}),
            id="alternates-not-an-array",
        ),
        pytest.param(
            _with_field("action_stamp", {"canonical_label": "A", "alternate_labels": []}),
            id="alternates-empty",
        ),
        pytest.param(
            _with_field("action_stamp", {"canonical_label": "A", "alternate_labels": ["B", "b"]}),
            id="alternates-repeat-under-folding",
        ),
        pytest.param(
            _with_field("action_stamp", {"canonical_label": "A", "alternate_labels": ["a"]}),
            id="canonical-listed-as-its-own-alternate",
        ),
    ],
)
def test_load_vocabulary_refuses_a_malformed_field_record(tmp_path: Path, payload: object) -> None:
    with pytest.raises(CodesError):
        load_vocabulary(_write(tmp_path / "vocab.json", payload))


def test_two_fields_sharing_one_canonical_label_are_refused(tmp_path: Path) -> None:
    """A shared label identifies no field, so every rule reading extracted text
    would be undecidable over it."""
    document = _valid()
    document["fields"]["action_stamp"]["canonical_label"] = document["fields"]["revision_suffix"][
        "canonical_label"
    ]
    with pytest.raises(CodesError):
        load_vocabulary(_write(tmp_path / "vocab.json", document))


def test_an_alternate_that_is_another_fields_canonical_label_is_refused(tmp_path: Path) -> None:
    """VR-035b's decidability, asserted in its failing direction."""
    document = _valid()
    document["fields"]["action_stamp"]["alternate_labels"] = [
        document["fields"]["revision_suffix"]["canonical_label"]
    ]
    with pytest.raises(CodesError):
        load_vocabulary(_write(tmp_path / "vocab.json", document))


def test_an_alternate_shared_by_two_fields_is_refused(tmp_path: Path) -> None:
    document = _valid()
    document["fields"]["action_stamp"]["alternate_labels"] = list(
        document["fields"]["revision_suffix"]["alternate_labels"]
    )
    with pytest.raises(CodesError):
        load_vocabulary(_write(tmp_path / "vocab.json", document))


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_mutated(structural_fields=["transmittal_number"]), id="structural-too-few"),
        pytest.param(
            _mutated(structural_fields=list(reversed(STRUCTURAL_FIELD_KEYS))),
            id="structural-out-of-order",
        ),
        pytest.param(_mutated(structural_fields="six"), id="structural-not-an-array"),
        pytest.param(_mutated(date_field_order=[]), id="dates-empty"),
        pytest.param(_mutated(date_field_order=["date_submitted"]), id="dates-too-few"),
        pytest.param(
            _mutated(date_field_order=["date_submitted", "date_submitted"]),
            id="dates-repeat",
        ),
        pytest.param(_mutated(date_field_order=[7, 8]), id="dates-not-strings"),
    ],
)
def test_load_vocabulary_refuses_a_malformed_field_list(tmp_path: Path, payload: object) -> None:
    with pytest.raises(CodesError):
        load_vocabulary(_write(tmp_path / "vocab.json", payload))


def test_a_named_field_with_no_entry_under_fields_is_refused(tmp_path: Path) -> None:
    document = _valid()
    document["fields"].pop("date_returned")
    document["date_field_order"] = list(DATE_KEYS)
    with pytest.raises(CodesError):
        load_vocabulary(_write(tmp_path / "vocab.json", document))
