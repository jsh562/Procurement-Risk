"""A deliberately violating package.

FR-023, FR-035. The real contract bars every module on the worklist's read path
from the model-provider gateway, so "the worklist renders when the provider is
unreachable" is a property of what is imported rather than a claim about what is
called. A provider dependency added to the read path fails the build instead of
surfacing as a slow page on a bad network day.

All three source modules violate it, because a fixture that broke only one would
leave the other two unevidenced — and the contract names three for a reason: the
read path is not one module.
"""
