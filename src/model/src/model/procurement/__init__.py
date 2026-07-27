"""Seeded generation, loading and validation of the synthetic procurement history (E005).

This package produces four committed artifacts from one recorded seed — the
dataset fixture `procurement-history.json`, its digest sidecar, the datasheet
that discloses how the fixture was made, and an isolated ground-truth record of
the vendor offsets a later fit is scored against — and writes the fixture's rows
into the `purchase_order_line` and `lifecycle_event` tables E003 delivered.

**It creates no database object.** Both tables, every constraint on them and
every migration are E003's and are fixed input here; this package writes rows
and alters nothing. The reserved migration block `0200`–`0299` is claimed and
goes unused.

**Nothing here re-authors what another package already publishes.** The
canonical serialization rule set — sorted keys, compact separators,
`ensure_ascii=False`, UTF-8 over *parsed* content rather than file bytes — lives
in `model.roster.reader.canonical_bytes` and is reused, not copied: it already
exists twice in this repository and a third copy is the defect rather than the
fix (AD-001). Digest primitives come from `model.corpus.manifest`, project and
vendor identities come from `model.roster.reader.read_roster()`, and the
real-firm exclusion list comes from `model.roster.naming`.

**Two digest conventions, deliberately, one per owner** (AD-010). The roster is
hashed by `roster.reader.content_hash` over canonical content, because that is
the value E001 publishes and the form `ck_pol__roster_hash_format` accepts. The
equipment category map is hashed by `corpus.manifest.sha256_of_file` over raw
bytes, because that is the value all five of E002's manifests already record for
that same file. Unifying them would put two different digests for one file into
the repository, so each `generation_inputs` entry records its own `digest_kind`
and a reader can tell which convention to recompute under.

**Nothing in the generation path reads a clock or the operating system's
entropy.** `generation_date`, `as_of_date` and the order-date window are
committed constants, and every surrogate key is a `uuid5` derived from the
natural key. `uuid.uuid4()` reads `os.urandom` and ignores the seed entirely, so
using it anywhere here would move the committed content hash on every run while
the recorded seed still appeared honoured — a silent defeat of the one property
this epic exists to hold (FR-021, HINT-001).

Modules, one concern each:

| Module | Concern |
|---|---|
| `paths` | Where the four emitted artifacts live |
| `model` | The closed envelope, line and event record types; `NS_E005` |
| `serialize` | Canonical bytes, the dataset content hash, the committed file layout |
| `allocate` | The declared vendor and project vectors and the PO grouping |
| `seeds` | Content-addressed per-line random streams |
| `durations` | Lognormal transition draws and the variance decomposition |
| `censor` | Truncation at the as-of date and the three shape floors |
| `criticality` | Slack, schedule pressure, and the tier × tercile band |
| `lifecycle` | The legal state walk and the declared rework allocation |
| `equipment` | Descriptive columns, the manufacturer name space, corpus overlap |
| `truth` | The isolated ground-truth record |
| `datasheet` | The seven-section disclosure |
| `generate` | `procurement-generate` |
| `load` | `procurement-load` |
| `validate` | `procurement-validate` |
"""
