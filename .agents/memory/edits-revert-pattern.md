---
name: Edit persistence on TradeAlgo Pro files
description: broker/quantity_calculator.py and advance_orb/app.py subtractive edits revert silently in this project — assert count, verify with sed, restart immediately.
---

In this project, single-shot edits to `broker/quantity_calculator.py` and `advance_orb/app.py` are unreliable — column-name-string edits, single import additions, and dict-key additions routinely lose partial changes between turns or get reverted to a previous shape when the buffer rewinds. Apply via a Python `io.open`/`str.replace` script that asserts the **old** pattern's `count == 1` before replacing, then **re-verify with grep/sed before restarting the workflow** — never trust the Edit tool's first attempt on these two files.

**Why:** every fix here was attempted ≥3 times before sticking. Whitespace, surrounding-context drift, and quote-style mismatches (single vs double quotes inside dict lookups like `df["SEM_EXM_EXCH_ID"]`) caused the patch to silently no-op. Pycache reinvalidation is also a pitfall: after restarts, `broker/__pycache__/quantity_calculator.cpython-*.pyc` re-compiles fine, but stale patterns in this same project persisted across multiple sessions.

**How to apply:** any future patch to those two files — pre-flight `sed -n '<range>' <file>` to read exact whitespace, assert `src.count(old) == 1`, replace once, sed-verify the new line before `WorkflowsRestart`. Treats of the file failing to match the assumed shape are the most common failure, not syntax errors.
