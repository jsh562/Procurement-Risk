"""One assertable value standing for the whole returned ordering.

Contract `RetrievalResponse.ordering_digest`, witnessing FR-020 (identical
ordering for identical queries against an unchanged corpus and configuration)
and SC-012 (each arm identical across two runs). Both are element-wise claims;
this makes them a single comparison, which is the difference between an
assertion a test can make cheaply and one it makes approximately.

**Digested after the deterministic route, not after fusion.** The route adds
matches outside `limit` and they are part of what the caller received, so a
digest taken at the fusion boundary would certify an ordering that is not the
one returned.

**`generated_at` takes no part in it.** It is the single member two responses to
an identical query may legitimately differ in; folding it in would make the
digest differ on every call and witness nothing.

**Deliberately not `api.compute.ordering.ordering_digest`.** That one is E010's,
typed over `UUID` po-line identifiers, and reusing it would put a retrieval
regression one edit to a worklist module away. The separator idiom is shared on
purpose and for the same reason: joining bare identifiers would let two
orderings of differently-split values collide, and a digest that can collide
answers "did the order change?" with a confident no.
"""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256

__all__ = ["ordering_digest"]

#: The digest of the empty sequence, stated once. Every empty result set carries
#: this value, so equality of the digest alone MUST NOT be read as sameness of
#: response -- two queries that found nothing agree here and may agree nowhere
#: else. The contract says so; naming the constant is what keeps a reader who
#: sees it twice in a log from concluding the responses were the same.
EMPTY_ORDERING_DIGEST = "sha256:" + sha256(b"").hexdigest()


def ordering_digest(chunk_ids: Iterable[str]) -> str:
    """Digest the ordered `chunk_id` sequence exactly as returned.

    Defined on every response including an empty one, where it is the digest of
    the empty sequence -- a response with no results still made an ordering
    claim, and leaving the member null would force every consumer to special-case
    the one case where the claim is trivially true.
    """
    joined = "\n".join(str(chunk_id) for chunk_id in chunk_ids)
    return "sha256:" + sha256(joined.encode("utf-8")).hexdigest()
