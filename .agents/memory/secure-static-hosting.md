---
name: Least-privilege frontend hosting
description: The security boundary to preserve when serving a static frontend alongside an API.
---

Serve only the known frontend entrypoint and asset directories from a web server. Do not mount the project or repository root, because backend source, configuration, and future secret-bearing files may become readable over HTTP.

**Why:** A broad static mount can expose files that were never intended to be public, even when the current repository contains no secret file.

**How to apply:** Prefer explicit file responses for the entry HTML and stylesheets plus narrowly scoped static mounts for asset directories. Keep API routes outside those mounts.