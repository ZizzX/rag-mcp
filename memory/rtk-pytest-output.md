---
name: rtk-pytest-output
description: "`uv run pytest` prints only \"Pytest: N passed\" and `-rf`/`grep FAILED` show nothing — the RTK hook summarises test output; redirect to a file to see failures"
metadata:
  node_type: memory
  type: reference
  originSessionId: 08bc9086-39d8-4981-89f6-cfea7a8a525a
  modified: 2026-10-17T18:49:51.960Z
---

The RTK Bash hook rewrites test commands and condenses their output. Piping `uv run pytest -q -rf | grep -E "^FAILED|^E "` can return nothing even when tests fail, and a green run shows as `Pytest: 33 passed`.

**Why:** 2026-10-16, T-014 final review: red-test reasons were invisible until the output went to a file.

**How to apply:** `uv run pytest -q -k … > "$TMPDIR/out.txt" 2>&1; grep -E "^FAILED|^E   " "$TMPDIR/out.txt"` (or `rtk proxy uv run pytest …` for the raw stream). Do not conclude "no failures" from an empty grep of piped output.
