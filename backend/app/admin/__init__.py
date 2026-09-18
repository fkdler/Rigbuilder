"""Read-only operability endpoints for local operators.

This package only reads: it never writes to the database and is not imported by the
Agent loop, the fusion service, the verifier or the tool layer, so enabling it cannot
change how a recommendation is produced.

Everything it reports was already being persisted by those layers; the gap this fills
is that ``agent_run.metrics`` carries 33 keys and had no query entry point at all.
"""
