import importlib
import sys


def test_broker_angel_ws_imports_without_smartapi():
    sys.modules.pop("broker.angel_ws", None)
    module = importlib.import_module("broker.angel_ws")
    assert hasattr(module, "latest_ticks")
    assert hasattr(module, "start_websocket")
