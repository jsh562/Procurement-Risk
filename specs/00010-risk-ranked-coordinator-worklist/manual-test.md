# Manual verification — E010 Risk-Ranked Coordinator Worklist

Scope: only what automation did **not** cover. The Playwright tier (19 specs) already
asserts FR-032's presentation contract and the named FR-048–FR-051 obligations. What
remains is the general WCAG surface no installed tool measures.

## Why this file exists

No accessibility tooling is installed and autopilot is off, so none was added:

| Tool | State | Install |
|---|---|---|
| axe-core CLI | absent | `npm i -D @axe-core/cli` |
| `@axe-core/playwright` | absent | `npm i -D @axe-core/playwright` (preferred — reuses the existing tier) |
| pa11y | absent | `npm i -D pa11y` |
| lighthouse | absent | `npm i -D lighthouse` |

Accessibility is not among the required QC categories (`linting`, `coverage`), so its
absence does not gate. It is recorded rather than passed over.

## Startup

```bash
docker compose up -d db

PYTHONUTF8=1 UV_NATIVE_TLS=1 \
DATABASE_URL='postgresql://procurement:local-development-only@localhost:5434/procurement' \
  uv run --directory src/api python tests/fixtures/frozen_run/seed.py
```

> This writes only to `procurement_e2e`, which it creates and migrates itself, and
> prints that URL when it finishes. The shared `procurement` database is never
> touched. It did truncate the shared database once — T057 fixed that, and this
> warning is what the old behaviour looked like.

```bash
# Serving boundary
PYTHONUTF8=1 UV_NATIVE_TLS=1 \
DATABASE_URL='postgresql://procurement:local-development-only@localhost:5434/procurement' \
WORKLIST_TIMEZONE=UTC WORKLIST_ALLOWED_ORIGINS=http://127.0.0.1:3000 \
  uv run --directory src/api uvicorn api.main:app --host 127.0.0.1 --port 8000

# Interface (separate shell)
cd src/web && rm -rf .next && \
WORKLIST_API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_WORKLIST_API_BASE_URL=http://127.0.0.1:8000 \
  npm run build && npm run start
```

Readiness: `curl -s http://127.0.0.1:8000/api/v1/worklist` returns JSON with
`counts.total == 15`.

Target: <http://127.0.0.1:3000/worklist>

## Scenarios

Each item states what to check and what a failure looks like.

### A1 — Colour contrast (WCAG 1.4.3, 1.4.11)

Not measured by any test. `page.module.css` uses `currentColor` borders and
`opacity: 0.85` on `.provenance`, `.tiebreak`, `.groupNote`. Check the 4.5:1 text
ratio and 3:1 non-text ratio in both the light and dark system themes.
**Fail**: any figure, state label or control label below ratio.

### A2 — Heading order and landmarks (WCAG 1.3.1, 2.4.6)

The page nests `h1` → `h2` (banner label, group headings) → `h3` (duration-pair
label). Confirm no level is skipped once a degraded banner and both groups render
together, and that `main` is the only top-level landmark.

### A3 — Reflow at 320 px and 200% zoom (WCAG 1.4.10, 1.4.4)

`.row` collapses to one column below 40rem. Confirm no horizontal scroll and no
clipped figure at 320 px wide, and at 200% browser zoom.

### A4 — Focus visibility (WCAG 2.4.7)

Nothing in the stylesheet sets `:focus-visible`, so the browser default applies.
Confirm the date input and both selects show a visible focus ring against the
page background in both themes.
**Fail**: focus indistinguishable from the unfocused state.

### A5 — Live-region announcement in a real screen reader

Playwright asserts the region's `role`, `aria-live` and text. It cannot confirm a
screen reader *speaks* it. With NVDA or VoiceOver, adjust a need-by date and
confirm the acknowledgement is announced without stealing focus, and that the
adjustment-status region is distinguishable from the page-scope banners (both are
`role="status"`; only the acknowledgement carries `aria-label="Adjustment status"`).

### A6 — Bounded forms spoken correctly

Row `PO-4476-2` displays `<1%`. Confirm a screen reader speaks it as a bound
("less than one percent") rather than as `1%`.
**Fail**: the bound is spoken as a flat figure — the exact defect FR-008 exists to
prevent, one layer below where the tests look.

### A7 — Degraded-state rows read intelligibly

Rows `PO-4473-1` (not covered), `PO-4473-2` (roster mismatch), `PO-4474-1/2`
(already late), `PO-4475-1` (calendar passed), `PO-4476-1` (beyond horizon).
Confirm each row's state label is reached in the row's reading order and that an
already-late row's absent probability is spoken as words, never as silence.

## Cleanup

```bash
# Stop both servers (Ctrl-C). Nothing else is required: the seed owns
# `procurement_e2e` and rebuilds it on each run, so leaving it in place is safe
# and costs one small database.

# To reclaim it:
MSYS_NO_PATHCONV=1 docker compose exec -T db \
  psql -U procurement -d postgres -c "DROP DATABASE IF EXISTS procurement_e2e;"
```

> An earlier version of this section deleted every row from `procurement` and
> reloaded E005's dataset, because the seed used to truncate the shared database.
> Since T057 it does not, so those commands destroyed E005's data for no reason.
> Removed rather than corrected in place — a cleanup step that is more dangerous
> than the thing it cleans up is worse than none.
