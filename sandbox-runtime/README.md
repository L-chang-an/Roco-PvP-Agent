# Chat Mode sandbox runtime

This environment is intentionally separate from the application environment. Build it during
deployment, never while serving a query:

```bash
uv sync --project sandbox-runtime --python 3.12 --frozen
```

Set `SANDBOX_RUNTIME_PYTHON` to `sandbox-runtime/.venv/bin/python` (or the equivalent immutable
deployment path). The runtime contains only CPython 3.12, NumPy and Pandas. The trusted runner and
the synthetic read-only `game_data` module are supplied by the host service.
