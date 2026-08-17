import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_tv_chart_candles_module_imports():
    module = importlib.import_module("tradingview.tv_ohlc_ws")
    assert hasattr(module, "batch_tv_opening_candles")
    assert hasattr(module, "batch_tv_confirmed_bar_ohlc")


def test_series_rows_parses_tv_payload():
    module = importlib.import_module("tradingview.tv_ohlc_ws")
    raw = (
        '~m~156~m~{"m":"series_data","p":["s0",{"s":[[1718900000,101.2,102.4,100.7,101.8,1200],[1718900400,101.8,103.0,101.0,102.6,1500]]}]}'
    )
    rows = module._series_rows(raw)
    assert rows == [
        [1718900000.0, 101.2, 102.4, 100.7, 101.8, 1200.0],
        [1718900400.0, 101.8, 103.0, 101.0, 102.6, 1500.0],
    ]

