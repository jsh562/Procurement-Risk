"""The network guard's public names, in a module with one identity.

Split out of `conftest.py` deliberately. pytest imports a conftest under the
module name `conftest`, so a test doing `from tests.conftest import ...` creates
a *second* module object from the same file — and the exception class the guard
raises is then a different class from the one the test catches. The guard fires,
`pytest.raises` does not match, and the failure reads as "the guard did not
work" when the guard worked perfectly.

That is not a hypothetical: it is what happened when this control was first
written. A plain module has one identity however it is reached, which is the
whole reason this file exists.
"""

from __future__ import annotations


class OutboundConnectionAttempted(AssertionError):
    """A test tried to open a network connection.

    An `AssertionError` rather than a custom base, so it reads as a failed
    assertion — which is what it is. The suite's whole claim is that it runs
    offline, and a connection attempt falsifies it wherever it came from.
    """


#: Destinations the guard permits. **Loopback only**, and only because the
#: database and the spool live there — the invocation record is written over a
#: socket, and forbidding all sockets would forbid the record.
#:
#: A provider is never at one of these, so permitting them does not weaken the
#: claim TR-067 makes: the model provider is the only external destination this
#: epic's code reaches, and nothing here can reach it.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
