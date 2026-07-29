"""The offline ingestion job: corpus documents in, citable chunks and traced
extracted values out (E006).

Every module here is reached through the `ingest` console entry declared in
`src/model/pyproject.toml`, never from a request-serving path.
"""
