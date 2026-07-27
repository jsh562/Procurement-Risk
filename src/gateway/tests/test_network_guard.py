"""TR-067 / SC-008: the guard fires, observed rather than assumed.

`tests/checks/test_no_outbound_egress.py` asserts the guard's *shape* — that it
exists, is autouse, patches both connect entry points, raises, and permits only
loopback. Every one of those is satisfied by a guard that never actually stops
anything.

This file is the behavioural half, and it has to live here: the fixture is
installed by this tier's `conftest.py`, so this is the only place it is really
in force. A root-level test would have to reproduce the guard to exercise it,
and would then be testing its copy while the installed one did nothing.
"""

from __future__ import annotations

import socket

import pytest
from netguard import LOOPBACK_HOSTS, OutboundConnectionAttempted


def test_an_outbound_connection_is_refused() -> None:
    """The measurement SC-008's zero rests on.

    `example.invalid` is reserved by RFC 2606 and resolves nowhere, so this test
    cannot reach a network even if the guard were removed — the assertion is
    that it fails *for the guard's reason*, not that it fails.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(
        OutboundConnectionAttempted
    ):
        sock.connect(("example.invalid", 443))


def test_the_non_raising_variant_is_refused_too() -> None:
    """`connect_ex` returns an error code instead of raising, so a client using
    it would slip past a guard that covered only `connect` — and the suite would
    report zero outbound requests while making them."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(
        OutboundConnectionAttempted
    ):
        sock.connect_ex(("example.invalid", 443))


def test_the_refusal_names_the_destination() -> None:
    """A guard that refused without saying where to would leave a developer
    grepping the suite for whatever made a request."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(
        OutboundConnectionAttempted, match="example.invalid"
    ):
        sock.connect(("example.invalid", 443))


@pytest.mark.parametrize("host", sorted(LOOPBACK_HOSTS))
def test_loopback_is_permitted(host: str) -> None:
    """The guard must not forbid everything: the invocation record is written
    over a socket, so a guard that blocked loopback would block the record and
    this epic's own database tier would be unrunnable.

    Connecting to a closed port raises `OSError` — that is the *operating
    system* refusing, which is the point: the guard let the attempt through.
    """
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as sock:
        sock.settimeout(0.2)
        try:
            sock.connect((host, 9))  # discard port, reliably closed
        except OutboundConnectionAttempted:  # pragma: no cover - the failure case
            pytest.fail(f"the guard refused loopback host {host!r}")
        except OSError:
            pass  # refused by the OS, which means the guard permitted it


def test_the_guard_is_in_force_for_this_test_without_being_requested() -> None:
    """Autouse, observed. This test declares no fixture and is still guarded —
    which is the property that makes SC-008 a claim about the suite rather than
    about the tests that remembered to ask."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(
        OutboundConnectionAttempted
    ):
        sock.connect(("198.51.100.7", 443))
