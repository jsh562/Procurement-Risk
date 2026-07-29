"""The served response, validated against the committed contract.

FR-057, and QC iteration 1's T064.

The contract declares closed objects — `additionalProperties: false` throughout
— and FR-057 binds three later epics to it. Before this, nothing machine-checked
a response against the document: closure rested on hand-written key-set
assertions covering `primary`, `secondary` and `unranked.primary`, and left
`miss_probability`, `duration_pair`, `meta`, `scope` and `sort` unchecked.

Hand-written key sets are not a bad way to assert the parts a requirement names
— they say *why* each key belongs, which a schema cannot. They are a bad way to
assert closure over a whole document, because the ones nobody wrote are exactly
the ones that drift. This module covers the second case; the named assertions
elsewhere stay for the first.

**The contract is the authority here, not this test.** If the two disagree the
question is which is right, and the answer is not automatically the code — that
is the point of publishing a contract three epics are told to build against.

The checker is `tests/conformance.py` rather than the `jsonschema` package.
That is not a preference: `jsonschema` is declared by `src/model`, and
`tests/checks/test_dependency_isolation.py` forbids a modeling distribution from
reaching the serving boundary's resolution. Adding it here broke that check, and
widening the shared-infrastructure allowlist to make a test pass would be
weakening an architecture contract for the convenience of the thing it
constrains.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from conformance import SUPPORTED_KEYWORDS, ConformanceError, validate

CONTRACT = (
    Path(__file__).resolve().parents[3]
    / "specs"
    / "00010-risk-ranked-coordinator-worklist"
    / "contracts"
    / "openapi.yaml"
)

AS_OF = date(2026, 6, 1)
TODAY = date(2026, 6, 3)


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    """The committed OpenAPI document."""
    assert CONTRACT.exists(), f"the contract is missing at {CONTRACT}"
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, 0, tzinfo=ZoneInfo("UTC"))


def _validate(document: dict[str, Any], body: dict[str, Any]) -> None:
    validate(body, "WorklistResponse", document)


def test_a_populated_response_conforms(
    frozen_run: dict[str, Any],
    client: Any,
    monkeypatch: Any,
    contract: dict[str, Any],
) -> None:
    """The nominal case — every ranked row, both groups, all nine members."""
    from api.routes import worklist as route

    monkeypatch.setattr(route, "now_in_zone", lambda: _at(TODAY))
    _validate(contract, client.get("/api/v1/worklist").json())


def test_the_no_active_run_response_conforms(
    empty_worklist: Any, client: Any, contract: dict[str, Any]
) -> None:
    """FR-015's state. `meta.forecast_run` is null here and the contract admits
    that explicitly — a schema that only ever saw the populated case would not
    have been checked against the state this feature was built around."""
    _validate(contract, client.get("/api/v1/worklist").json())


def test_the_empty_filter_response_conforms(
    frozen_run: dict[str, Any], client: Any, contract: dict[str, Any]
) -> None:
    """FR-042's state — both groups empty, scope populated."""
    body = client.get("/api/v1/worklist", params={"project_id": "PRJ-009"}).json()
    assert "empty_filter" in body["page_states"]
    _validate(contract, body)


def test_an_adjusted_response_conforms(
    frozen_run: dict[str, Any],
    client: Any,
    monkeypatch: Any,
    contract: dict[str, Any],
) -> None:
    """FR-031's session what-if — `overrides.applied` populated, a row carrying
    `source: session_override` and `unsaved: true`."""
    from api.routes import worklist as route

    monkeypatch.setattr(route, "now_in_zone", lambda: _at(TODAY))
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    adjusted = date.fromisoformat(line["need_by_date"]) - timedelta(days=5)

    body = client.get(
        "/api/v1/worklist",
        params={"need_by_override": [f"{line['po_line_id']}:{adjusted.isoformat()}"]},
    ).json()
    assert body["overrides"]["applied"], "precondition: the override must have applied"
    _validate(contract, body)


def test_an_unapplied_override_conforms(
    frozen_run: dict[str, Any], client: Any, contract: dict[str, Any]
) -> None:
    """FR-055's report — the `unapplied` arm with its cause enum."""
    from uuid import uuid4

    body = client.get(
        "/api/v1/worklist", params={"need_by_override": [f"{uuid4()}:2026-09-01"]}
    ).json()
    assert body["overrides"]["unapplied"], "precondition: the override must be unapplied"
    _validate(contract, body)


def test_the_validator_rejects_an_undeclared_member(
    frozen_run: dict[str, Any],
    client: Any,
    monkeypatch: Any,
    contract: dict[str, Any],
) -> None:
    """The check that keeps the five above from being vacuous.

    `additionalProperties: false` is the contract's whole closure mechanism, and
    a validator wired up wrongly — an unresolvable `$ref`, a registry the schema
    never consults — would accept anything and report five green tests. Adding a
    key the contract does not declare must fail.
    """
    from api.routes import worklist as route

    monkeypatch.setattr(route, "now_in_zone", lambda: _at(TODAY))
    body = client.get("/api/v1/worklist").json()

    body["ranked"][0]["primary"]["expected_delivery_date"] = "2026-08-10"
    with pytest.raises(ConformanceError, match="does not conform"):
        _validate(contract, body)


def test_the_validator_rejects_a_figure_the_contract_forbids(
    frozen_run: dict[str, Any],
    client: Any,
    monkeypatch: Any,
    contract: dict[str, Any],
) -> None:
    """The second half of the same guard, aimed at what this product refuses.

    `PercentFigure.percent` is `integer | null` with a stated range; a raw float
    is exactly the encoding FR-053 forbids, because it invites the client to
    round a second time.
    """
    from api.routes import worklist as route

    monkeypatch.setattr(route, "now_in_zone", lambda: _at(TODAY))
    body = client.get("/api/v1/worklist").json()

    row = next(r for r in body["ranked"] if r["primary"]["miss_probability"] is not None)
    row["primary"]["miss_probability"]["miss"]["percent"] = 34.7
    with pytest.raises(ConformanceError, match="does not conform"):
        _validate(contract, body)


def test_the_checker_covers_every_construct_the_contract_uses(
    contract: dict[str, Any],
) -> None:
    """The guard a hand-written checker actually needs.

    Its real failure mode is not a wrong answer — it is silently ignoring a
    keyword it does not implement, which turns every conformance test above
    into a weaker assertion without any of them changing colour. So the
    contract's own keyword set is compared against what the checker implements.

    A construct added to `openapi.yaml` that this module does not handle fails
    here, naming itself, rather than being skipped.
    """
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            seen.update(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(contract["components"]["schemas"])

    # Member names are not keywords. Every key under a `properties` object names
    # a member of the payload, at any depth — collected recursively, because a
    # schema nested two levels down contributes names too.
    member_names: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            member_names.update(node.get("properties", {}))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(contract["components"]["schemas"])

    unsupported = seen - SUPPORTED_KEYWORDS - member_names - set(contract["components"]["schemas"])
    assert not unsupported, (
        f"contracts/openapi.yaml uses constructs tests/conformance.py does not implement: "
        f"{sorted(unsupported)}. Implement them or the conformance tests silently weaken."
    )


def test_the_checker_rejects_a_null_where_the_contract_declares_an_object(
    contract: dict[str, Any],
) -> None:
    """The `oneOf` nullable idiom is the subtlest construct here, and the one a
    hand-written checker is most likely to get wrong in the permissive
    direction. `counts` is not nullable; `meta.forecast_run` is."""
    with pytest.raises(ConformanceError):
        validate({"meta": None}, "WorklistResponse", contract)
