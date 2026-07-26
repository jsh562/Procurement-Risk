"""Reads the client off a permitted module. No direct import edge exists, so
the allowlist contract is satisfied and only the source scan objects."""

from reexport_fixture.wrapper import client

anthropic = client
