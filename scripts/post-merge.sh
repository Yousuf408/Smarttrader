#!/bin/bash
set -e

echo "=== Post-merge setup ==="

# Dependencies are pre-installed via the Nix environment.
# Just verify basic Python + critical libs work.
python3 -c "import fastapi, uvicorn, pandas, yfinance, pyotp, requests, httpx, pydantic; print('Core imports OK')"

echo "=== Setup complete ==="
