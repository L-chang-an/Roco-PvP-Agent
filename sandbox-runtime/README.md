# Chat Mode sandbox runtime

This environment is intentionally separate from the application environment. Build it during
deployment, never while serving a query:

```bash
uv sync --project sandbox-runtime --python 3.12 --frozen
```

Set `SANDBOX_RUNTIME_PYTHON` to `sandbox-runtime/.venv/bin/python` (or the equivalent immutable
deployment path). The runtime contains only CPython 3.12, NumPy and Pandas. The trusted runner and
the synthetic read-only `game_data` module are supplied by the host service.

The native Windows backend uses LPAC, a read-only runtime, an isolated desktop,
and Job limits. The approved compatibility policy includes `registryRead` and
does not enable Win32k lockdown. A Windows venv redirector cannot be used directly:
prepare a standalone bundle with `python -m roco_pvp_agent.sandbox.windows_runtime`.
See [Windows preparation and acceptance results](WINDOWS.md) before enabling it.
The documented runtime passed the latest Windows conformance and 100-query
stability run with a 120-second wall budget. Earlier native crashes were not
reproduced in that run; their root cause remains undetermined.
