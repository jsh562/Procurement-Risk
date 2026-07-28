"""NC-10 / SC-032 — an out-of-pin version produces a report, not a claim.

FR-022 scopes the reproducibility claim to the pinned library version. Running
under a different one does not make the dataset wrong; it makes the claim
*unverified*. Reporting that honestly is the requirement, and the observed
version is an injected parameter precisely so this can be demonstrated without
reinstalling numpy.
"""

from __future__ import annotations

import numpy as np
import pytest

from model.procurement import paths
from model.procurement.serialize import read_payload
from model.procurement.validate import (
    ValidationError,
    check_provenance_agreement,
    scope_limit,
    validate,
)


@pytest.fixture
def envelope():
    return read_payload(paths.fixture_path())


class TestInsideThePin:
    def test_no_scope_limit_applies(self, envelope) -> None:
        assert scope_limit(envelope, np.__version__) == []

    def test_the_recorded_pin_is_the_resolved_version(self, envelope) -> None:
        """DV-025: the datasheet must not publish a pin nothing ran under."""
        assert envelope["library_pin"]["numpy"] == np.__version__

    def test_validation_claims_reproduction(self) -> None:
        report = validate(observed_numpy=np.__version__)
        assert report.reproduction_claimed
        assert report.scope_limits == []
        assert report.dataset_content_hash.startswith("sha256:")


class TestOutsideThePin:
    @pytest.mark.parametrize("observed", ["1.26.4", "2.0.0", "99.99.99"])
    def test_a_scope_limit_is_reported(self, envelope, observed: str) -> None:
        limits = scope_limit(envelope, observed)
        assert len(limits) == 1
        assert observed in limits[0]
        assert envelope["library_pin"]["numpy"] in limits[0]

    def test_the_report_says_unverified_rather_than_refuted(self, envelope) -> None:
        """The distinction the requirement turns on. 'Refuted' would be a claim
        about a run that never happened."""
        message = scope_limit(envelope, "99.99.99")[0]
        assert "unverified" in message
        assert "not performed" in message

    def test_no_reproduction_claim_is_made(self) -> None:
        report = validate(observed_numpy="99.99.99")
        assert not report.reproduction_claimed
        assert report.scope_limits

    def test_the_input_checks_still_run(self) -> None:
        """A scope limit on the *library* says nothing about the inputs, so
        skipping their verification would be an unrelated loss of coverage."""
        report = validate(observed_numpy="99.99.99")
        assert report.inputs_checked == 3

    def test_the_run_does_not_fail(self) -> None:
        """Out of pin is a scope limit, not an error: the dataset is not wrong."""
        report = validate(observed_numpy="99.99.99")
        assert report.dataset_content_hash.startswith("sha256:")


class TestProvenanceAgreement:
    def test_a_pin_disagreeing_with_the_resolved_version_is_refused(self, envelope) -> None:
        with pytest.raises(ValidationError, match="nothing ran under"):
            check_provenance_agreement(envelope, None, "0.0.1")

    def test_matching_datasheet_values_pass(self, envelope) -> None:
        datasheet = {
            name: envelope[name]
            for name in (
                "generator_id",
                "generator_revision",
                "root_seed",
                "seed_derivation",
                "generation_date",
                "as_of_date",
            )
        }
        check_provenance_agreement(envelope, datasheet, np.__version__)

    def test_a_disagreeing_datasheet_value_is_refused(self, envelope) -> None:
        datasheet = {
            name: envelope[name]
            for name in (
                "generator_id",
                "generator_revision",
                "root_seed",
                "seed_derivation",
                "generation_date",
                "as_of_date",
            )
        }
        datasheet["root_seed"] = envelope["root_seed"] + 1
        with pytest.raises(ValidationError, match="disagrees with the artifact"):
            check_provenance_agreement(envelope, datasheet, np.__version__)

    def test_a_missing_datasheet_field_is_refused(self, envelope) -> None:
        datasheet = {"generator_id": envelope["generator_id"]}
        with pytest.raises(ValidationError, match="omits the provenance field"):
            check_provenance_agreement(envelope, datasheet, np.__version__)
