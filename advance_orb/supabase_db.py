"""
Supabase storage for strategy results.

Saves the top 5 symbols per strategy per day so we can build a
historical performance dashboard (STRATEGY, TRADES, WIN RATE,
TOTAL P&L, AVG RETURN) later.

One file that owns all Supabase logic — strategy backends just call
``save_top5_strategy(name, rows)`` and move on.
"""

from datetime import date, datetime, timezone
from typing import Any, Literal

from supabase import create_client, Client

# ── Credentials (hardcoded for portability, same pattern as ANGEL_PROXIES) ──
_SUPABASE_URL = "https://atyqkbrmrosnoczktsmm.supabase.co"
_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImF0eXFrYnJtcm9zbm9jemt0c21tIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1NjI4ODcsImV4cCI6MjA5NjEzODg4N30.f-vn85HGFfPMUNeyJLccZSIVTKvZGXp1Ty5Hw08pFsU"

# Lazy-init client so import doesn't fail when offline
_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    return _client


# ── Table schema (auto-created via SQL) ──

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS strategy_trades (
    id          BIGSERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    strategy    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    buy_price   NUMERIC(12,2),
    stop_loss   NUMERIC(12,2),
    target_1_2  NUMERIC(12,2),
    max_qty     INTEGER,
    gain_per_lakh NUMERIC(12,2),
    avg_return  NUMERIC(6,2),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (date, strategy, symbol)
);
"""


def ensure_table():
    """Create ``strategy_trades`` if it doesn't already exist.

    Uses an unauthenticated raw SQL post to the Supabase REST API.
    If the anon key doesn't have DDL permissions the user will need to
    create the table manually via the Supabase SQL Editor.
    """
    import httpx

    try:
        resp = httpx.post(
            f"{_SUPABASE_URL}/rest/v1/rpc/",
            json={"query": _CREATE_TABLE_SQL},
            headers={
                "apikey": _SUPABASE_KEY,
                "Authorization": f"Bearer {_SUPABASE_KEY}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.is_success:
            return True
        print(f"⚠️  Table creation returned HTTP {resp.status_code}")
        return False
    except Exception as e:
        print(f"⚠️  Could not auto-create strategy_trades: {e}")
        return False


def _safe_float(val) -> float | None:
    """Convert a value to float or return None."""
    if val is None:
        return None
    try:
        v = float(val)
        return round(v, 2)
    except (TypeError, ValueError):
        return None


# ── Public API ──

StrategyName = Literal["advanceorb", "bigplayers"]


def save_top5_strategy(
    strategy: StrategyName,
    rows: list[dict[str, Any]],
):
    """Save the top **5** symbols from a strategy result to Supabase.

    Args:
        strategy: Strategy identifier (``"advanceorb"`` or ``"bigplayers"``).
        rows: Strategy result rows (pre-sorted, first 5 are saved).
    """
    if not rows:
        return {"ok": False, "error": "No rows to save"}

    top5 = rows[:5]
    today = date.today()
    client = _get_client()

    saved = 0
    skipped = 0
    errors: list[str] = []

    for row in top5:
        symbol = row.get("Symbol") or row.get("symbol")
        if not symbol:
            continue

        # Common candle anchors
        high915 = _safe_float(row.get("high915"))
        low915 = _safe_float(row.get("low915"))

        # ── Compute entry (buy) price from strategy-specific auto-buy rules ──
        if strategy == "advanceorb":
            # Buy when price moves 0.12% above the 9:15 high (breakout trigger)
            buy_price = round(high915 * 1.0012, 2) if high915 and high915 > 0 else None
        elif strategy == "bigplayers":
            # Buy when price recovers to ≥ 75% of the 9:15 candle range above low
            if high915 and low915 and high915 > low915 and low915 > 0:
                range_ = high915 - low915
                buy_price = round(low915 + range_ * 0.75, 2)
            else:
                buy_price = None
        else:
            buy_price = _safe_float(row.get("Price") or row.get("price"))

        # Stop loss = 9:15 low
        sl = low915

        # Compute derived fields (1:2 risk-reward from entry price)
        target_1_2 = None
        gain_per_lakh = None
        avg_return = None
        if buy_price and sl and buy_price > 0 and buy_price > sl:
            risk = buy_price - sl
            target_1_2 = round(buy_price + 2 * risk, 2)
            # Gain per ₹1L invested
            qty = int(100000 / buy_price)
            gain_per_lakh = round(qty * (target_1_2 - buy_price), 2)
            avg_return = round(((target_1_2 - buy_price) / buy_price) * 100, 2)

        max_qty = row.get("MaxQty") or row.get("max_qty") or 0
        try:
            max_qty = int(max_qty)
        except (TypeError, ValueError):
            max_qty = 0

        record = {
            "date": today.isoformat(),
            "strategy": strategy,
            "symbol": symbol,
            "buy_price": buy_price,
            "stop_loss": sl,
            "target_1_2": target_1_2,
            "max_qty": max_qty,
            "gain_per_lakh": gain_per_lakh,
            "avg_return": avg_return,
        }

        try:
            # Check if this date+strategy+symbol combo already exists
            existing = (
                client.table("strategy_trades")
                .select("id")
                .eq("date", record["date"])
                .eq("strategy", record["strategy"])
                .eq("symbol", record["symbol"])
                .limit(1)
                .execute()
            )
            if existing.data:
                skipped += 1
                continue

            result = client.table("strategy_trades").insert(record).execute()
            if result.data:
                saved += 1
            else:
                skipped += 1
        except Exception as e:
            errors.append(f"{symbol}: {e}")
            skipped += 1

    msg = f"Saved {saved}/{len(top5)} rows, skipped {skipped}"
    if errors:
        msg += f", errors: {'; '.join(errors[:3])}"
    print(f"[supabase_db] {strategy}: {msg}")
    return {"ok": True, "saved": saved, "skipped": skipped, "errors": errors}


def get_history(strategy: StrategyName | None = None, limit: int = 100):
    """Fetch historical strategy trades for the dashboard.

    Args:
        strategy: Optional filter by strategy name.
        limit: Max rows to return.

    Returns:
        List of trade records, newest first.
    """
    client = _get_client()
    try:
        query = (
            client.table("strategy_trades")
            .select("*")
            .order("date", desc=True)
            .order("id", desc=True)
            .limit(limit)
        )
        if strategy:
            query = query.eq("strategy", strategy)
        result = query.execute()
        return result.data or []
    except Exception as e:
        print(f"[supabase_db] get_history error: {e}")
        return []


def get_summary(strategy: StrategyName | None = None):
    """Aggregate stats per strategy — trades, win rate, total P&L, avg return.

    Returns a list of dicts matching the UI table:
        STRATEGY, TRADES, WIN RATE, TOTAL P&L, AVG RETURN
    """
    client = _get_client()
    try:
        query = client.table("strategy_trades").select("*")
        if strategy:
            query = query.eq("strategy", strategy)
        result = query.execute()
        rows = result.data or []
    except Exception as e:
        print(f"[supabase_db] get_summary query error: {e}")
        return []

    from collections import defaultdict

    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r.get("strategy", "unknown")].append(r)

    summaries = []
    for strat, trades in groups.items():
        total = len(trades)
        # A trade is a "win" if it has positive avg_return
        wins = sum(1 for t in trades if (t.get("avg_return") or 0) > 0)
        win_rate = round((wins / total) * 100) if total > 0 else 0
        total_pnl = sum(t.get("gain_per_lakh") or 0 for t in trades)
        avg_ret = (
            round(sum(t.get("avg_return") or 0 for t in trades) / total, 1)
            if total > 0
            else 0
        )

        summaries.append(
            {
                "strategy": strat,
                "name": {"advanceorb": "Advance ORB", "bigplayers": "Big Players"}.get(
                    strat, strat.title()
                ),
                "trades": total,
                "win_rate": win_rate,
                "total_pnl": round(total_pnl, 2),
                "avg_return": avg_ret,
            }
        )

    return summaries
