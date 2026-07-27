"""Fixture package: the provider-facing module reaching arithmetic two ways.

Stands in for E004's computation-boundary contract (TR-032). The real one
forbids `gateway.provider` from reaching `gateway.compute`, because cost,
content hashing and duration are the first real arithmetic in the repository
and must stay where they can be property-tested.
"""
