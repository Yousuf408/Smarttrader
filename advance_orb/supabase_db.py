"""
Supabase storage for strategy results.

Saves the top 5 symbols per strategy per day so we can build a
historical performance dashboard (STRATEGY, TRADES, WIN RATE,
TOTAL P&L, AVG RETURN) later.

One file that owns all Supabase logic + strategy-specific
entry/target rules — strategy backends just call
``save_top5_strategy(name, rows)`` and move on.
"""

from datetime import date
from typing import Any, Literal

from supabase import create_client, Client

# ── Credentials (hardcoded for portability, same pattern as ANGEL_PROXIES) ──
_SUPABASE_URL = "https://atyqkbrmrosnoczktsmm.supabase.co"
_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImF0eXFrYnJtcm9zbm9jemt0c21tIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1NjI4ODcsImV4cCI6MjA5NjEzODg4N30.f-vn85HGFfPMUNeyJLccZSIVTKvZGXp1Ty5Hw08pFsU"

# ── Table schema (matching the manually created table) ──
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

# Lazy-init client so import doesn't fail when offline
_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    return _client


def _safe_float(val) -> float | None:
    """Convert a value to float or return None."""
    if val is None:
        return None
    try:
        v = float(val)
        return round(v, 2)
    except (TypeError, ValueError):
        return None


def ensure_table():
    """Create ``strategy_trades`` if it doesn't already exist.

    Uses the Supabase management API. If the anon key doesn't have DDL
    permissions the user needs to create the table manually via the SQL Editor.
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
        return False
    except Exception as e:
        print(f"⚠️  Could not auto-create strategy_trades: {e}")
        return False


# ── Public API ──

StrategyName = Literal["advanceorb", "bigplayers"]


def save_top5_strategy(
    strategy: StrategyName,
    rows: list[dict[str, Any]],
):
    """Save the top **5** symbols from a strategy result to Supabase.

    Computes entry price, stop-loss, target, gain per ₹1L and avg
    return using each strategy's auto-buy rules:

    - **advanceorb**: entry = high915 × 1.0012, target = 1:2 RR
    - **bigplayers**: entry = low915 + range × 0.75, target = 2% above entry

    Args:
        strategy: ``"advanceorb"`` or ``"bigplayers"``.
        rows: Strategy result rows (pre-sorted, first 5 are saved).
    """
    if not rows:
        return {"ok": False, "error": "No rows to save"}

    top5 = rows[:5]
    today = date.today().isoformat()
    client = _get_client()

    saved = 0
    skipped = 0
    errors: list[str] = []

    for row in top5:
        symbol = row.get("Symbol") or row.get("symbol")
        if not symbol:
            continue

        high915 = _safe_float(row.get("high915"))
        low915 = _safe_float(row.get("low915"))

        # ── Compute entry (buy) price ──
        buy_price = None
        if strategy == "advanceorb" and high915 and high915 > 0:
            buy_price = round(high915 * 1.0012, 2)
        elif strategy == "bigplayers" and high915 and low915 and high915 > low915:
            range_ = high915 - low915
            buy_price = round(low915 + range_ * 0.75, 2)

        # Stop loss = 9:15 low
        sl = low915

        # ── Compute target and returns ──
        target = None
        gain_per_lakh = None
        avg_return = None

        if buy_price and buy_price > 0:
            if strategy == "bigplayers":
                target = round(buy_price * 1.02, 2)
            elif sl and buy_price > sl:
                risk = buy_price - sl
                target = round(buy_price + 2 * risk, 2)

            if target and target > buy_price:
                qty = int(100000 / buy_price)
                gain_per_lakh = round(qty * (target - buy_price), 2)
                avg_return = round(((target - buy_price) / buy_price) * 100, 2)

        max_qty = int(row.get("MaxQty", 0) or row.get("max_qty", 0))

        record = {
            "date": today,
            "strategy": strategy,
            "symbol": symbol,
            "buy_price": buy_price,
            "stop_loss": sl,
            "target_1_2": target,
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
