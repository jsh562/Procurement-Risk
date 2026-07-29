"""US3 — every degraded state, end to end.

The independent test the task list states: load the worklist in each of the
eight degraded states and confirm each renders its own distinct wording with no
risk figure fabricated.

The eight are states of what the system *knows*, each reached by a successful
read, so every one of them is a `200` carrying its state. Reporting one as an
error would make an honest answer indistinguishable from an outage — the same
defect in the opposite direction to rendering an outage as an empty state.

FR-043's three conditions are the other side of that line and are asserted here
too, precisely because they are *not* a ninth state: the system did not read the
artifacts, so it holds no knowledge about any line to report.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

AS_OF = date(2026, 6, 1)
HORIZON = 365


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, 0, tzinfo=ZoneInfo("UTC"))


@pytest.fixture
def at_today(monkeypatch: Any) -> Any:
    """Move the server's `today` without waiting for it (FR-038)."""

    def _set(day: date) -> None:
        from api.routes import worklist as route

        monkeypatch.setattr(route, "now_in_zone", lambda: _at(day))

    _set(AS_OF + timedelta(days=2))
    return _set


def _rows(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for row in body["ranked"] + body["unranked"]:
        identity = row["primary"]["identity"]
        out[f"{identity['po_number']}-{identity['line_number']}"] = row
    return out


class TestEveryStateIsATwoHundred:
    """FR-018. All eight are successful outcomes carrying their state."""

    def test_no_active_run(self, empty_worklist: Any, client: Any, at_today: Any) -> None:
        response = client.get("/api/v1/worklist")
        assert response.status_code == 200
        assert "no_active_run" in response.json()["page_states"]

    def test_stale_run(self, frozen_run: dict[str, Any], client: Any, at_today: Any) -> None:
        """FR-029. Eight days past the anchor: one day past a seven-day
        threshold compared strictly."""
        at_today(AS_OF + timedelta(days=8))
        body = client.get("/api/v1/worklist").json()
        assert "stale_run" in body["page_states"]
        assert body["meta"]["forecast_run"]["stale"] is True
        assert body["meta"]["forecast_run"]["age_days"] == 8

    def test_a_run_at_the_threshold_is_not_stale(
        self, frozen_run: dict[str, Any], client: Any, at_today: Any
    ) -> None:
        """The near side of the same boundary. Without it, `>` and `>=` are
        indistinguishable and the threshold is off by a day."""
        at_today(AS_OF + timedelta(days=7))
        body = client.get("/api/v1/worklist").json()
        assert "stale_run" not in body["page_states"]
        assert body["meta"]["forecast_run"]["stale"] is False

    def test_the_staleness_claim_travels_with_its_basis(
        self, frozen_run: dict[str, Any], client: Any, at_today: Any
    ) -> None:
        """FR-029. An unexplained threshold is a number a coordinator cannot
        argue with; the basis is what makes "stale" a claim rather than a
        verdict."""
        run = client.get("/api/v1/worklist").json()["meta"]["forecast_run"]
        assert run["staleness_threshold_days"] == 7
        assert "refit cadence" in run["staleness_basis"]

    def test_empty_filter(self, frozen_run: dict[str, Any], client: Any, at_today: Any) -> None:
        body = client.get("/api/v1/worklist", params={"project_id": "PRJ-009"}).json()
        assert "empty_filter" in body["page_states"]
        assert body["counts"]["total"] == 0

    def test_not_covered(self, frozen_run: dict[str, Any], client: Any, at_today: Any) -> None:
        row = _rows(client.get("/api/v1/worklist").json())["PO-4473-1"]
        assert row["state"] == "not_covered"

    def test_roster_mismatch(self, frozen_run: dict[str, Any], client: Any, at_today: Any) -> None:
        row = _rows(client.get("/api/v1/worklist").json())["PO-4473-2"]
        assert row["state"] == "roster_mismatch"

    def test_beyond_horizon(self, frozen_run: dict[str, Any], client: Any, at_today: Any) -> None:
        row = _rows(client.get("/api/v1/worklist").json())["PO-4476-1"]
        assert row["state"] == "beyond_horizon"

    def test_already_late(self, frozen_run: dict[str, Any], client: Any, at_today: Any) -> None:
        rows = _rows(client.get("/api/v1/worklist").json())
        assert rows["PO-4474-1"]["state"] == "already_late"
        assert rows["PO-4474-2"]["state"] == "already_late"

    def test_calendar_passed(self, frozen_run: dict[str, Any], client: Any, at_today: Any) -> None:
        row = _rows(client.get("/api/v1/worklist").json())["PO-4475-1"]
        assert row["state"] == "calendar_passed"


class TestWhatEachStateWithholds:
    """FR-054's two encodings, and which applies where."""

    def test_an_excluded_row_has_no_property_that_could_hold_a_figure(
        self, frozen_run: dict[str, Any], client: Any, at_today: Any
    ) -> None:
        """Structural absence, used where nothing may ever be shown.

        A row with a nullable `miss_probability` renders a dash, and a dash
        reads as a figure — so the property is absent from the shape entirely.
        """
        for identifier in ("PO-4473-1", "PO-4473-2"):
            row = _rows(client.get("/api/v1/worklist").json())[identifier]
            assert set(row["primary"]) == {"identity", "need_by"}
            assert "secondary" not in row

    def test_an_already_late_row_carries_an_explicit_empty_and_keeps_the_rest(
        self, frozen_run: dict[str, Any], client: Any, at_today: Any
    ) -> None:
        """The other encoding, used where a figure exists in the general case
        and this row's state removed it.

        The distinction is deliberate. A structural absence here would take the
        quantile pair with it, and the quantile pair is the whole remaining
        answer: the miss probability is 1 by construction and says nothing,
        while how much *further* slip is coming is still open.
        """
        row = _rows(client.get("/api/v1/worklist").json())["PO-4474-1"]
        assert row["primary"]["miss_probability"] is None
        assert row["primary"]["duration_pair"]["median"]["days"] >= 0
        assert row["secondary"]["criticality"] >= 1

    def test_no_state_produces_a_zero_a_dash_or_a_sentinel(
        self, frozen_run: dict[str, Any], client: Any, at_today: Any
    ) -> None:
        """FR-054. A zero in a payload is the dash-on-a-screen defect one
        renderer earlier, where nobody is looking for it."""
        body = client.get("/api/v1/worklist").json()

        for row in body["ranked"]:
            figure = row["primary"]["miss_probability"]
            if figure is None:
                continue
            for direction in ("miss", "on_time"):
                percent = figure[direction]["percent"]
                display = figure[direction]["display"]
                assert display not in {"0%", "100%", "", "-", "—", "N/A"}
                assert percent != 0
                assert percent != 100

        for row in body["unranked"]:
            assert "miss_probability" not in row["primary"]

    def test_the_beyond_horizon_row_states_a_bound_and_keeps_its_place(
        self, frozen_run: dict[str, Any], client: Any, at_today: Any
    ) -> None:
        """FR-017. Expected harm comes from the draws and does not depend on the
        survival grid, so the line stays ranked; only the probability degrades."""
        body = client.get("/api/v1/worklist").json()
        ranked = {row["po_line_id"] for row in body["ranked"]}
        row = _rows(body)["PO-4476-1"]

        assert row["po_line_id"] in ranked
        assert row["primary"]["miss_probability"]["measure"] == "upper_bound"


class TestFailuresAreNotStates:
    """FR-043. Three conditions, none of them a ninth degraded state."""

    def test_an_unreadable_artifact_schema_version_is_refused_by_name(
        self, frozen_run: dict[str, Any], client: Any, connection: Any, at_today: Any
    ) -> None:
        """A reader meeting an unfamiliar schema version fails loudly rather
        than misreading array offsets — and a misread offset yields a figure
        wrong in a way no coordinator could see.

        Reported with its own cause rather than as a generic fault, because
        "this run was written by a schema this build does not know" names what
        would change it and a bare failure does not.
        """
        with connection.cursor() as cursor:
            cursor.execute("UPDATE forecast_run SET artifact_schema_version = 99")

        response = client.get("/api/v1/worklist")
        assert response.status_code == 500

        detail = response.json()["detail"]
        assert detail["type"].endswith("/unsupported-artifact-schema")
        assert detail["artifact_schema_version"] == 99
        assert detail["correlation_id"]
        assert "99" in detail["detail"]

    def test_a_refusal_carries_no_row_and_no_figure(
        self, frozen_run: dict[str, Any], client: Any, connection: Any, at_today: Any
    ) -> None:
        """FR-043. It must display no row and no figure, because the system
        holds no knowledge about any line to report."""
        with connection.cursor() as cursor:
            cursor.execute("UPDATE forecast_run SET artifact_schema_version = 99")

        body = client.get("/api/v1/worklist").json()
        # The whole body, not a check for two absent keys: a refusal that
        # carried an empty `ranked` alongside its problem document would satisfy
        # "no row" and would still hand a client something to render a list
        # from, which is how an outage starts looking like an empty worklist.
        assert set(body) == {"detail"}
        serialised = repr(body).lower()
        for forbidden in ("po_line_id", "miss_probability", "duration_pair", "%"):
            assert forbidden not in serialised

    def test_the_refusal_wording_differs_from_the_no_active_run_wording(
        self, frozen_run: dict[str, Any], client: Any, connection: Any, at_today: Any
    ) -> None:
        """FR-043. One means the system looked and there was nothing there; the
        other means it could not look. Rendering them alike presents an outage
        as an honest empty state."""
        with connection.cursor() as cursor:
            cursor.execute("UPDATE forecast_run SET artifact_schema_version = 99")

        detail = client.get("/api/v1/worklist").json()["detail"]
        assert "no forecast" not in detail["title"].lower()
        assert "not recognised" in detail["title"].lower()

    def test_two_failures_carry_different_correlation_identifiers(
        self, frozen_run: dict[str, Any], client: Any, connection: Any, at_today: Any
    ) -> None:
        """Two identical requests failing an hour apart are two incidents. A
        shared identifier would defeat the point, which is to find *this*
        occurrence in the record."""
        with connection.cursor() as cursor:
            cursor.execute("UPDATE forecast_run SET artifact_schema_version = 99")

        first = client.get("/api/v1/worklist").json()["detail"]["correlation_id"]
        second = client.get("/api/v1/worklist").json()["detail"]["correlation_id"]
        assert first != second
