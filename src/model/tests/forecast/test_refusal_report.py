"""T092 — DV-038 / SC-033 / FR-037: the emitted refusal report and its field set.

Asserted over the **emitted file** and not over the stream. The stream is
transient while **G-8** makes the pair the only surviving record of a refusal —
a refused run writes no row anywhere, by design — and a disclosure that rests on
a file nothing inspects rests on nothing. Writing the file is not a write to any
store SC-015 quantifies over, so this and DV-013 are compatible by construction
rather than by exception.

Both refusal shapes, because the report's content differs between them and each
half fails on its own. A **post-sampling** breach carries every breached
blocking diagnostic with its `parameter_name` where the metric is
parameter-scoped, its realized value, its threshold and its **threshold
direction**, plus the attempt's realized sampling shape. A **pre-sampling**
refusal carries the precondition and its realized value, and records that
nothing was sampled.

`check_refusal_report` is the predicate, and it lives here so
`test_refusal_report_controls.py` runs *this* function against its plants. A
control that re-authored the predicate would show the copy is falsifiable and
say nothing about the original.
"""

from __future__ import annotations

import re
from pathlib import Path

from forecast.conftest import RefusedInvocation
from model.forecast.diagnostics import PARAMETER_METRICS
from model.forecast.paths import REFUSAL_REPORT_PREFIX, REPORT_SUFFIX
from model.forecast.report import (
    NOTHING_SAMPLED,
    REFUSAL_DIAGNOSTIC_FIELDS,
    REFUSAL_PRECONDITION_FIELDS,
    REFUSAL_SECTION_TITLES,
)

#: The fields the attempt's own identity section must carry. FR-037 builds the
#: filename from three of them and the file restates all three in full, so a
#: reader holding the file never has to parse its own name.
IDENTITY_FIELDS = ("Attempt identifier", "As-of date", "Input row hash", "Attempted at", "Reason")

#: How a section heading and a per-breach heading are spelled by the renderer.
SECTION_PATTERN = re.compile(r"^## \d+\. (?P<title>.+)$", re.MULTILINE)
BREACH_PATTERN = re.compile(r"^### (?P<subject>.+)$", re.MULTILINE)

#: The two sections whose bodies are field sets rather than prose.
PRECONDITION_SECTION = "Unmet Preconditions"
DIAGNOSTIC_SECTION = "Breached Blocking Diagnostics"


def report_sections(text: str) -> dict[str, str]:
    """The report split into its declared sections, by heading.

    Parsed from the rendered text rather than from the renderer's own structure,
    because the claim is about what a reader of the file gets — a section the
    renderer built and did not emit is a field a reader is owed and does not.
    """
    matches = list(SECTION_PATTERN.finditer(text))
    bounds = [match.start() for match in matches] + [len(text)]
    return {
        match.group("title"): text[match.end() : bounds[index + 1]]
        for index, match in enumerate(matches)
    }


def check_refusal_report(text: str) -> None:
    """DV-038's predicate: the declared schema, and every field set complete.

    One function over both refusal shapes. Which sections carry content depends
    on where the refusal happened, but the *schema* does not: all four sections
    are rendered on every refusal, because a report whose preconditions section
    is absent and one whose preconditions were all met are indistinguishable to
    a reader, and telling those apart is what this file is read for.
    """
    sections = report_sections(text)

    assert list(sections) == list(REFUSAL_SECTION_TITLES), (
        f"the report declares sections {list(sections)} rather than "
        f"{list(REFUSAL_SECTION_TITLES)}"
    )
    for field in IDENTITY_FIELDS:
        assert f"**{field}**" in sections["Refused Attempt"], (
            f"the report carries no {field!r}; a refused attempt has no `run_id`, so these "
            f"fields are the whole of how its evidence is identified (FR-037)"
        )
    sampling = sections["Sampling"]

    assert "**Wall clock**" in sampling
    assert NOTHING_SAMPLED in sampling or "post-warmup draws" in sampling, (
        "the Sampling section records neither a realized shape nor that nothing was "
        "sampled; FR-037 requires one or the other, and the difference is FR-035's "
        "evidence against FR-017's"
    )
    _check_precondition_blocks(sections[PRECONDITION_SECTION])
    _check_diagnostic_blocks(sections[DIAGNOSTIC_SECTION])


def _check_precondition_blocks(body: str) -> None:
    """Each unmet precondition as its two-field set, plus the verdict."""
    if body.strip().startswith("None"):
        return
    for field in REFUSAL_PRECONDITION_FIELDS:
        assert f"**{field}**" in body, (
            f"an unmet precondition is recorded without its {field!r}; the two-field set "
            f"is the precondition and the value that failed it"
        )


def _check_diagnostic_blocks(body: str) -> None:
    """Every breach block, each with FR-017's complete five-field set.

    Per block rather than over the section as a whole, because a section
    containing one complete set and forty incomplete ones satisfies any
    whole-document search — and "every breached blocking diagnostic carries its
    own set" is exactly the clause that would be lost.
    """
    if body.strip().startswith("None"):
        return
    headings = list(BREACH_PATTERN.finditer(body))

    assert headings, "the section reports breaches and renders no block for any of them"
    bounds = [match.start() for match in headings] + [len(body)]
    for index, heading in enumerate(headings):
        block = body[heading.end() : bounds[index + 1]]
        metric = heading.group("subject").split(" on ")[0].strip()
        required = [
            field
            for field in REFUSAL_DIAGNOSTIC_FIELDS
            if field != "Parameter" or metric in PARAMETER_METRICS
        ]
        for field in required:
            assert f"**{field}**" in block, (
                f"the breach block for {heading.group('subject')!r} carries no {field!r}. "
                f"A value and a bar do not resolve to a verdict without the direction, and "
                f"a bare metric name does not say which parameter breached"
            )


def _only_report(invocation: RefusedInvocation) -> Path:
    """The single file the attempt emitted, proved to be exactly one."""
    emitted = invocation.emitted_reports

    assert len(emitted) == 1, (
        f"the attempt emitted {[path.name for path in emitted]}; FR-037 makes it one file "
        f"per attempt and FR-040 closes the emitted set to three kinds"
    )
    return emitted[0]


def test_a_post_sampling_refusal_emits_a_report_named_for_the_attempt(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """FR-037's naming rule, checked against the file that actually landed.

    A refused attempt has no `run_id`, so the identifier is built from the as-of
    date, the input row hash and the attempt's timestamp — which is what makes
    two refusals of one input distinguishable, the case a retry loop produces
    and the case the history matters most in.
    """
    emitted = _only_report(refused_after_sampling)

    assert emitted.name.startswith(f"{REFUSAL_REPORT_PREFIX}-")
    assert emitted.suffix == REPORT_SUFFIX
    assert refused_after_sampling.as_of_date.isoformat() in emitted.name, (
        f"{emitted.name} does not carry the attempt's as-of date; the identifier names "
        f"what the run was asked to forecast, what it read, and when it tried"
    )


def test_the_post_sampling_report_carries_the_whole_field_set(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """DV-038: the same field set as the stderr reason, on the durable half."""
    check_refusal_report(_only_report(refused_after_sampling).read_text(encoding="utf-8"))


def test_the_post_sampling_report_records_the_realized_shape(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """The attempt's realized shape, not the published one.

    FR-037 asks for what the attempt actually reached, and an attempt refused at
    a shape other than the committed one is precisely where the realized and the
    published figures differ. A report quoting the constants would describe a
    run nobody performed.
    """
    text = _only_report(refused_after_sampling).read_text(encoding="utf-8")
    sampling = report_sections(text)["Sampling"]

    assert f"{refused_after_sampling.chain_count} chains" in sampling
    assert f"{refused_after_sampling.draws_per_chain} draws" in sampling
    assert NOTHING_SAMPLED not in sampling, (
        "a report for a refusal that sampled records that nothing was sampled; the two "
        "refusal classes leave different evidence and this is where they are told apart"
    )


def test_every_breach_on_the_stream_appears_in_the_file(
    refused_after_sampling: RefusedInvocation,
) -> None:
    """The pair G-8 names says the same thing on both halves.

    The stream is where an operator sees the refusal and the file is what
    survives it, so a file carrying fewer breaches than the message would leave
    the durable record weaker than the transient one — which is the shape DV-038
    exists to forbid.
    """
    text = _only_report(refused_after_sampling).read_text(encoding="utf-8")
    body = report_sections(text)[DIAGNOSTIC_SECTION]
    in_file = len(BREACH_PATTERN.findall(body))
    on_stream = refused_after_sampling.completed.stderr.count(" — breached")

    assert in_file > 1
    assert in_file == on_stream, (
        f"the file records {in_file} breach(es) against {on_stream} on standard error"
    )


def test_a_pre_sampling_refusal_emits_a_report_recording_that_nothing_was_sampled(
    refused_below_the_chain_minimum: RefusedInvocation,
) -> None:
    """The other half of DV-038, and the half SC-036's evidence lives in.

    The precondition and its realized value, with no threshold direction because
    a precondition is not a measured metric — and the record that nothing was
    sampled, which is the fact that separates this refusal from NC-1's.
    """
    text = _only_report(refused_below_the_chain_minimum).read_text(encoding="utf-8")
    sections = report_sections(text)

    check_refusal_report(text)
    assert NOTHING_SAMPLED in sections["Sampling"]
    assert "**Precondition**" in sections[PRECONDITION_SECTION]
    assert "**Realized value**" in sections[PRECONDITION_SECTION]
    assert sections[DIAGNOSTIC_SECTION].strip().startswith("None"), (
        "the report of a pre-sampling refusal cites a breached blocking diagnostic; "
        "nothing was measured, so there is nothing for it to have breached"
    )


def test_the_zero_open_line_refusal_reports_its_own_precondition(
    refused_with_no_open_line: RefusedInvocation,
) -> None:
    """FR-021's refusal emits a report too, and names its own condition.

    The standing obligation reaches every refusal in the spec, not only the two
    that quantify over Published Constants rows — and FR-021's precondition is a
    property of the input rather than of a published bar, which is why it is
    stated separately and checked separately.
    """
    text = _only_report(refused_with_no_open_line).read_text(encoding="utf-8")

    check_refusal_report(text)
    assert "at least one line open at the as-of date" in text
    assert NOTHING_SAMPLED in report_sections(text)["Sampling"]


def test_two_refusals_of_one_input_are_two_files(
    refused_after_sampling: RefusedInvocation,
    refused_below_the_chain_minimum: RefusedInvocation,
) -> None:
    """One file **per attempt**, never overwritten by a later refusal.

    The two attempts read the same rows at the same as-of date, so their
    identifiers differ only in the timestamp component — which is exactly the
    case FR-037 says must both survive, and exactly the case a name built from
    the input alone would collapse.
    """
    first = _only_report(refused_after_sampling)
    second = _only_report(refused_below_the_chain_minimum)

    assert first.name != second.name
    assert first.read_bytes() != second.read_bytes()
