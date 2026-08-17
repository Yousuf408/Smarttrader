# Smarttrader Quick-Start Optimization (30 min)

## Step 1: Extract Configuration Layer (IMMEDIATE)

### Create `/workspaces/Smarttrader/config/` folder structure

```bash
mkdir -p config
touch config/__init__.py
touch config/defaults.py
touch config/strategies.py
touch config/broker.py
```

### `config/defaults.py`
**Purpose**: Centralize all hardcoded constants
```python
from dataclasses import dataclass

@dataclass
class MarketConfig:
    """Market and trading hour settings."""
    MARKET_OPEN_HOUR = 9
    MARKET_OPEN_MIN = 15
    MARKET_CLOSE_HOUR = 15
    MARKET_CLOSE_MIN = 45
    IST_OFFSET_HOURS = 5
    IST_OFFSET_MINS = 30
    
@dataclass
class CacheConfig:
    """Cache and persistence settings."""
    TV_SCAN_TTL = 600  # 10 minutes
    YF_BATCH_TIMEOUT = 25.0
    TV_CANDLE_TTL = 20 * 60 * 60
    TV_C2_CACHE_TTL = 20 * 60 * 60
    TV_LOGIN_COOLDOWN_S = 5 * 60
    
@dataclass
class DataConfig:
    """Data source settings."""
    PRICE_MIN = 200
    PRICE_MAX = 4000
    GAP_THRESHOLD = 2.0
    MARKET_CAP_MIN = 41_000_000_000
    EMA_SPAN = 200
    EMA_LOOKBACK_DAYS = 4
    SMALL_CANDLE_THRESHOLD = 1.5
    MAX_TV_STOCKS = 100
    YFINANCE_WORKERS = 4
    
@dataclass
class BrokerConfig:
    """Broker-specific settings."""
    DHAN_TOKEN_TTL_SECONDS = 24 * 3600
    DHAN_AUTO_RENEW_LEAD_SECONDS = 1 * 3600
    ANGEL_TOKEN_TTL_SECONDS = 24 * 3600
    ANGEL_AUTO_RENEW_LEAD_SECONDS = 12 * 3600

# Global config instance
DEFAULT_CONFIG = {
    'market': MarketConfig(),
    'cache': CacheConfig(),
    'data': DataConfig(),
    'broker': BrokerConfig(),
}

def get_config(section: str = None):
    """Get configuration by section, or all if None."""
    if section:
        return DEFAULT_CONFIG.get(section)
    return DEFAULT_CONFIG
```

### `config/strategies.py`
```python
"""Strategy-specific configuration."""
from typing import TypedDict

class StrategyConfig(TypedDict):
    name: str
    icon: str
    columns: list[str]
    color: str

STRATEGIES = {
    "advanceorb": StrategyConfig(
        name="Advance ORB",
        icon="📈",
        columns=[
            "Symbol", "Price", "CHG%", "GAP%", "Volume", "RELVOL",
            "Sector", "200 EMA", "1st High", "1st Low", "1st Range%",
            "Inside 9:15", "Share Low", "MaxQty"
        ],
        color="#6C5CE7"
    ),
    "bigplayers": StrategyConfig(
        name="Big Players",
        icon="🏢",
        columns=[
            "Symbol", "Price", "CHG%", "Breakout", "Support Price",
            "9:15 High", "9:15 Low", "Entry Price", "SL", "MaxQty", "Risk ₹"
        ],
        color="#A29BFE"
    ),
    "equalow": StrategyConfig(
        name="Equal Low",
        icon="📊",
        columns=[
            "Symbol", "Price", "CHG%", "Status", "Low", "Match", "Diff %"
        ],
        color="#00B894"
    ),
}

def get_strategy_config(strategy_id: str) -> StrategyConfig:
    """Get strategy config or raise KeyError."""
    if strategy_id not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_id}")
    return STRATEGIES[strategy_id]
```

### `config/__init__.py`
```python
from .defaults import get_config, DEFAULT_CONFIG, MarketConfig, DataConfig, BrokerConfig, CacheConfig
from .strategies import STRATEGIES, get_strategy_config

__all__ = [
    'get_config', 
    'DEFAULT_CONFIG',
    'MarketConfig',
    'DataConfig',
    'BrokerConfig',
    'CacheConfig',
    'STRATEGIES',
    'get_strategy_config',
]
```

---

## Step 2: Update Imports (Replace hardcoded values)

### Before:
```python
# advance_orb/common.py
PRICE_MIN = 200
PRICE_MAX = 4000
GAP_THRESHOLD = 2.0
MARKET_CAP_MIN = 41_000_000_000
EMA_SPAN = 200
```

### After:
```python
# advance_orb/common.py
from config import get_config

cfg = get_config('data')
PRICE_MIN = cfg.PRICE_MIN
PRICE_MAX = cfg.PRICE_MAX
GAP_THRESHOLD = cfg.GAP_THRESHOLD
MARKET_CAP_MIN = cfg.MARKET_CAP_MIN
EMA_SPAN = cfg.EMA_SPAN
```

---

## Step 3: Create Broker Base Interface

### `broker/base.py`
```python
"""Abstract base class for all brokers."""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass

@dataclass
class OrderRequest:
    symbol: str
    quantity: int
    transaction_type: str  # "buy" or "sell"
    price: float
    trigger_price: Optional[float] = None
    order_type: str = "MARKET"
    product_type: str = "MIS"
    
@dataclass
class OrderResponse:
    success: bool
    order_id: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None

class Broker(ABC):
    """Base interface for all brokers."""
    
    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with broker. Return True if successful."""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if broker is currently connected."""
        pass
    
    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a single order."""
        pass
    
    @abstractmethod
    def get_margin(self) -> Dict[str, float]:
        """Get available margin and funds."""
        pass
    
    @abstractmethod
    def calculate_quantity(self, symbol: str, budget: float, margin_per_share: float) -> int:
        """Calculate max quantity for a symbol given budget."""
        pass
```

### `broker/__init__.py`
```python
"""Broker management."""
from .base import Broker, OrderRequest, OrderResponse

__all__ = ['Broker', 'OrderRequest', 'OrderResponse']
```

---

## Step 4: Create Data Source Abstraction

### `strategies/data/models.py`
```python
"""Data models for strategy inputs."""
from dataclasses import dataclass
from typing import Optional

@dataclass
class StockData:
    """Unified stock data from any source."""
    symbol: str
    price: float
    yesterday_close: float
    yesterday_high: float
    yesterday_low: float
    day_high: float
    day_low: float
    volume: float
    relative_volume: float
    market_cap: float
    sector: str
    change_pct: float
    gap_pct: float
    ema200: Optional[float] = None

@dataclass
class CandleData:
    """OHLCV candle."""
    timestamp: float  # Unix timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
```

---

## Step 5: Consolidate Strategy Constants

### `strategies/config.py`
```python
"""Strategy-specific configuration and rules."""
from config import get_strategy_config as _get_strategy

# Advance ORB entry rules
ORB_SMALL_CANDLE_MAX = 1.5  # %
ORB_HIGH_BREAK_MIN_GAP = 0.15  # %
ORB_HIGH_BREAK_MAX_GAP = 0.50  # %
ORB_REQUIRE_ABOVE_EMA = True

# Big Players entry rules
BP_SUPPORT_LOW_PRICE_MIN = 200
BP_RANGE_BREAKOUT_MIN = 0.75  # 75% of range

# Equal Low rules
EL_TOLERANCE_PCT = 0.08
EL_LOOKBACK_CANDLES = 5
```

---

## Quick Test

After creating these files, verify imports work:

```bash
cd /workspaces/Smarttrader
python3 -c "
from config import get_config, get_strategy_config, STRATEGIES
cfg = get_config('data')
print(f'PRICE_MIN: {cfg.PRICE_MIN}')
print(f'STRATEGIES: {list(STRATEGIES.keys())}')
print('✅ Config layer working!')
"
```

---

## Next Steps After This (Week 2)

1. **Extract broker submodules**
   - Move `broker/quantity_calculator.py` → `broker/dhan/qty.py`
   - Move `broker/angel_margin_calculator.py` → `broker/angel/qty.py`
   - Create `broker/manager.py` to route between brokers

2. **Split `app.py`**
   - `api/v1/strategies.py` - Strategy endpoints
   - `api/v1/orders.py` - Order endpoints
   - `api/v1/broker.py` - Broker connection
   - Keep main `app.py` for lifespan + middleware only

3. **Create unified data layer**
   - `strategies/data/cache.py` - Unified caching
   - `strategies/data/sources.py` - TradingView + Yahoo + WebSocket

---

## Benefits You'll See Immediately

✅ **Cleaner imports** — Use `from config import get_config()` everywhere
✅ **Single source of truth** — Change a setting once, affects entire app
✅ **Easier testing** — Mock `get_config()` in tests
✅ **Better organization** — No more hunting for constants
✅ **Reduced code duplication** — ~150 lines removed from common.py

