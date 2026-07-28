"""Fixture package: forecast code reaching the model provider two ways.

FR-024 / FR-025 / DV-022. The real contract — `model.forecast` may reach neither
`model.llm` nor `gateway`, with `allow_indirect_imports = false` — passes on a
correct tree, and a contract naming a module that behaves is green whether the
contract works or not. This tree plants both violations the real one exists to
catch, so the failing direction has committed evidence that runs on every
triggering push and pull request.

It cannot live inside the real contract's root package: every contract runs over
its whole root, so a committed violation placed there would break the build
instead of demonstrating that the contract works.
"""
