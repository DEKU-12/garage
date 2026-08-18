"""FastAPI event server (TAD §5.1). Build week: 3.

    GET  /api/runs
    GET  /api/runs/{id}/events?after_seq=
    GET  /api/runs/{id}/artifacts/{path}   (path-sanitized to the run dir)
    WS   /ws/live/{run_id}

Reads the filesystem only -- never imports the engine (ADR-6).
Never crashes on a bad run dir.
"""
