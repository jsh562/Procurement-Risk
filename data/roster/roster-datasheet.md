# Datasheet — Project and Vendor Roster

**Status: SYNTHETIC.** Every project and vendor named in this roster is invented. No entry
corresponds to a real construction project, a real firm, or a real place. Nothing here was
derived from proprietary, confidential, or licensed material.

This datasheet accompanies `project-vendor-roster.json` and is required to sit alongside it:
a synthetic dataset that travels without its provenance eventually gets mistaken for a real
one, and the mistake is unrecoverable once downstream data has been generated from it.

## Motivation and Composition

The roster exists because two later epics need to agree on the same population before either
runs. E002 synthesizes project documents and E005 generates procurement history; if each
invented its own names, the corpus and the procurement records would describe different
worlds and no join between them would mean anything.

| Population | Count | Identifier scheme |
|---|---|---|
| Projects | 5 | `PRJ-###`, zero-padded, assigned in declaration order |
| Vendors | 12 | `VND-###`, zero-padded, assigned in declaration order |

Each entry carries exactly two fields: a stable identifier and a display name. Lifecycle
values — dates, quantities, lead times, prices — are deliberately absent. Those belong to
E005, which generates them against this roster; putting them here would fix in a fixture what
the modeling work is supposed to produce.

The population sizes are a judgment, not a measurement: five projects and twelve vendors are
enough for a vendor to appear across several projects and for per-vendor history to
accumulate, while staying small enough to read in full during review.

## Generation Process

Names were composed by hand against the convention committed in `naming-convention.json`,
which expresses both patterns as regular expressions so conformance is checked rather than
reviewed. Project names pair an invented placename with a facility type. Vendor names pair an
invented compound stem — carrying one of a small set of fictional suffixes — with a
trade-descriptive noun.

The convention is built to fail closed. Its stems and placenames are chosen precisely because
no operating firm or municipality uses them, so a name that satisfies the pattern is fictional
by construction rather than by having survived a search.

`real-firm-exclusions.json` is a second, weaker line: a sorted, unique list of real firms that
must never appear, compared after normalization. It is explicitly a backstop. A list of real
firms can never be complete, so it can only catch a name someone thought to add — which is
why the convention, not the list, is the primary defence.

## Uses and Distribution

Intended use is demonstration and evaluation of the procurement-risk pipeline. The roster is
consumed by exactly one reader, in the offline modeling boundary; the request-serving
boundary never reads it, because the serving image's build context excludes the data
directory entirely.

Both consuming epics record the reader-computed content hash alongside every artifact they
generate. That hash is what makes drift detectable rather than merely recordable: if the
roster changes after data has been generated from it, the recorded value and the current value
disagree and every artifact carrying the earlier one is stale by definition.

**No hash value is reproduced in this document.** A digest copied into prose is a second
source of truth that nothing updates, and a stale digest here would be indistinguishable from
real drift. The reader computes it; this datasheet describes it.

There are no distribution restrictions. The roster contains no personal data, no proprietary
content, and nothing attributable to an identifiable individual or organization.

## Maintenance and Out-of-Scope Content

Changing this roster is a breaking change for every artifact already generated from it. The
response is regeneration, not a warning: on a hash mismatch, the artifacts carrying the earlier
value are regenerated and re-recorded, or the roster change is reverted. E001 ships detection
only — there is no reconciliation mechanism and no automatic correction.

Explicitly out of scope for this dataset:

- Real firm names, real project names, and real places — excluded by construction.
- Contact details, addresses, personnel, and any other personal data — no field carries them.
- Prices, lead times, quantities, and delivery dates — owned by E005.
- Any lifecycle or status attribute; the roster is a declaration of identity only.
