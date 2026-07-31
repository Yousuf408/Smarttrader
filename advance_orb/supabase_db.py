"""
Supabase storage for strategy results.

Generic storage layer — just insert, query, and summary.
Strategy-specific business logic (entry price, target rules)
lives in the individual strategy files.
"""

from datetime import date
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


# ── Public API ──

StrategyName = Literal["advanceorb", "bigplayers"]

def _safe_float(val) -> float | None:
    """Convert a value to float or return None."""
    if val is None:
        return None
    try:
        v = float(val)
        return round(v, 2)
    except (TypeError, ValueError):
        return None


def save_trades(
    strategy: StrategyName,
    trades: list[dict[str, Any]],
):
    """Save a list of pre-computed trade records to Supabase.

    Each trade dict must have:
        symbol, buy_price, stop_loss, target, max_qty,
        gain_per_lakh, avg_return

    Args:
        strategy: ``"advanceorb"`` or ``"bigplayers"``.
        trades: Pre-computed trade records (only first 5 are saved).
    """
    if not trades:
        return {"ok": False, "error": "No trades to save"}

    top5 = trades[:5]
    today = date.today().isoformat()
    client = _get_client()

    saved = 0
    skipped = 0
    errors: list[str] = []

    for t in top5:
        symbol = t.get("symbol")
        if not symbol:
            continue

        record = {
            "date": today,
            "strategy": strategy,
            "symbol": symbol,
            "buy_price": _safe_float(t.get("buy_price")),
            "stop_loss": _safe_float(t.get("stop_loss")),
            "target_1_2": _safe_float(t.get("target")),
            "max_qty": int(t.get("max_qty", 0)),
            "gain_per_lakh": _safe_float(t.get("gain_per_lakh")),
            "avg_return": _safe_float(t.get("avg_return")),
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

    msg = f"Saved {saved}/{len(top5)} trades, skipped {skipped}"
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
