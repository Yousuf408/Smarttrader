# TradeAlgo Pro

## Overview

TradeAlgo Pro is a vanilla HTML/CSS/JavaScript trading dashboard with a FastAPI backend for the Advance ORB stock screener.

## Running on Replit

The project uses the `Start application` workflow, which runs the FastAPI app on port 5000. The API serves the existing frontend from the project root, so the dashboard and API use the same origin.

To run it manually:

```bash
uvicorn advance_orb.app:app --host 0.0.0.0 --port 5000
```

The Advance ORB endpoint queries TradingView through `tradingview-screener`, so its results depend on TradingView availability and network access. The other dashboard strategies use the data already included in the frontend.

## User preferences

- Keep the existing vanilla HTML/CSS/JavaScript and FastAPI structure unless a future request explicitly asks for a migration.
- Always include a "Modified files" section at the end of implementation updates for reference.