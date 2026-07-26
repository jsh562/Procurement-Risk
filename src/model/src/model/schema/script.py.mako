## Revision template for the modeling entry's forward-only migration chain.
##
## Lines beginning with "##" are Mako comments: they document the template for
## whoever edits it and are stripped before the revision file is written.
##
## TR-004 -- FILENAME PREFIXES. E003 owns 0001-0099; E004 owns 0100-0199.
## Alembic has no sequential-counter token, so the four-digit prefix is carried
## as the revision id itself and emitted by the `%%(rev)s` in `file_template`
## (see alembic.ini). Always pass the id explicitly:
##
##     alembic -c alembic.ini revision --rev-id 0003 -m "document"
##         -> src/model/src/model/schema/versions/0003_document.py
##
## Omitting --rev-id gets you a partial GUID, a filename outside the reserved
## block, and a failing test_migration_chain.py (T011).
##
## TR-002 -- FORWARD-ONLY. `downgrade()` raises and stays that way. Do not
## replace the raise with a reverse operation, and do not enable Alembic's
## downgrade built-ins: this project does not maintain downgrades, and an
## untested reverse body is worse than an honest refusal because it invites a
## caller to trust it.
##
## Autogenerate is not used -- env.py sets `target_metadata = None` and the
## schema is authored as explicit DDL -- so the `upgrades` and `imports` hooks
## below are always empty in practice. They are kept so the template stays
## valid if a revision is ever generated with --autogenerate.
##
## A freshly generated stub trips Ruff F401 on `op` until its `upgrade()` body
## is written. That is the intended nudge: an empty revision is unfinished work
## and is not something to commit. Fill the body rather than dropping the
## import -- `ruff check --fix` would happily delete it.
<%!
def _lit(value):
    """`repr()` biased toward double quotes, so output satisfies `ruff format`.

    Alembic's stock template uses bare `repr()`, which emits single-quoted
    strings that the formatter then rewrites -- turning every generated
    revision into an immediate `ruff format --check` failure.
    """
    rendered = repr(value)
    return rendered if '"' in rendered else rendered.replace("'", '"')


def _revises(value):
    """The value half of the `Revises:` header line, empty for the base revision.

    Written as a whole (leading space included) rather than as a filter on the
    value, because a stock `Revises: ${down_revision}` leaves trailing
    whitespace on the line when there is no parent -- which the formatter also
    rewrites.
    """
    if not value:
        return ""
    return " " + (value if isinstance(value, str) else ", ".join(value))
%>\
"""${message}

Revision ID: ${up_revision}
Revises:${_revises(down_revision)}
Create Date: ${create_date}

"""

from collections.abc import Sequence

from alembic import op
% if imports:
${imports}
% endif

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = ${_lit(up_revision)}
down_revision: str | Sequence[str] | None = ${_lit(down_revision)}
branch_labels: str | Sequence[str] | None = ${_lit(branch_labels)}
depends_on: str | Sequence[str] | None = ${_lit(depends_on)}


def upgrade() -> None:
    """Apply this revision.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.
    """
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only.

    TR-002. Kept as a raising stub rather than deleted, because Alembic calls
    this attribute when a downgrade is requested and a missing one would fail
    with an unexplained AttributeError instead of stating the policy.
    """
    raise NotImplementedError(
        "This migration is forward-only (TR-002) and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup."
    )
