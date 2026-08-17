# Smarttrader Structure Optimization Plan

## Phase 1: Backend Reorganization

### 1.1 Consolidate Broker Logic
**Current Problem**: Broker code scattered across multiple files
```
broker/
├── quantity_calculator.py       (Dhan creds, token management)
├── angel_margin_calculator.py   (Angel auth, calc)
├── angel_ws.py                  (WebSocket)
├── dhan_ws.py                   (Dhan feed)
├── dhan_orders.py               (Order placement)
└── angel_orders.py              (Order placement)
```

**Proposed Structure**:
```
broker/
├── __init__.py
├── base.py                      # Abstract broker interface
├── config.py                    # Centralized creds + config
├── dhan/
│   ├── __init__.py
│   ├── auth.py                  # TOTP + login
│   ├── margin.py                # Qty calculator
│   ├── orders.py                # Order placement
│   └── feed.py                  # WebSocket
├── angel/
│   ├── __init__.py
│   ├── auth.py
│   ├── margin.py
│   ├── orders.py
│   └── ws.py                    # WebSocket
└── manager.py                   # Broker factory + routing
```

**Benefits**:
- ✅ Clear ownership per broker
- ✅ Easy to add new brokers
- ✅ Centralized credential management

---

### 1.2 Split Strategies into Separate Modules
**Current Problem**: Strategy logic in routes + candle_recorder + common.py
```
advance_orb/
├── app.py (1890 lines!)
├── common.py
├── tv_chart_candles.py
├── candle_recorder.py
└── equal_low_scanner.py
```

**Proposed Structure**:
```
strategies/
├── __init__.py
├── base.py                      # Strategy interface
├── config.py                    # Strategy constants
├── data/
│   ├── __init__.py
│   ├── sources.py               # TradingView + Yahoo + WebSocket
│   ├── cache.py                 # Unified caching layer
│   └── filters.py               # Gap, price, EMA filters
├── advance_orb/
│   ├── __init__.py
│   ├── strategy.py              # Core logic
│   ├── routes.py                # API endpoints
│   └── validators.py            # Entry/exit rules
├── big_players/
│   ├── __init__.py
│   ├── strategy.py
│   ├── routes.py
│   └── validators.py
└── equal_low/
    ├── __init__.py
    ├── strategy.py
    └── routes.py
```

**Benefits**:
- ✅ Each strategy is self-contained
- ✅ Shared data layer reduces duplication
- ✅ Easy to add new strategies

---

### 1.3 Create Config Management System
**Current Problem**: Hardcoded values in multiple files
```python
# In common.py
PRICE_MIN = 200
PRICE_MAX = 4000

# In candle_tracker.py
BOOTSTRAP_MAX_SECONDS = 60.0
EMA_SPAN = 200

# In app.py
...repeated definitions...
```

**Proposed Structure**:
```
config/
├── __init__.py
├── base.py                      # Base config class
├── defaults.py                  # Fallback defaults
├── strategies.py                # Strategy-specific settings
├── broker.py                    # Broker settings
├── data.py                      # Data source settings
└── loader.py                    # .env + YAML loader
```

**Example**:
```python
# config/strategies.py
class AdvanceOrbConfig:
    PRICE_MIN = 200
    PRICE_MAX = 4000
    GAP_THRESHOLD = 2.0
    SMALL_CANDLE_THRESHOLD = 1.5
    EMA_SPAN = 200
    # All in ONE place
```

---

### 1.4 Unified Data Layer
**Current Problem**: Fetching from TradingView + Yahoo + WebSocket inconsistently
```
Current:
  app.py calls fetch_tradingview_stocks()
  → routes call batch_yahoo_orb_data()
  → candle_tracker.get_200_ema()
  → Three different sources!
```

**Proposed Structure**:
```
strategies/data/
├── adapter.py                   # Unified interface
├── sources/
│   ├── tradingview.py           # TV scanner
│   ├── yahoo.py                 # yfinance
│   ├── websocket.py             # Angel WS
│   └── cache.py                 # Unified cache management
└── models.py                    # StockData, CandleData DTOs
```

---

## Phase 2: API Organization

### 2.1 Split Routes by Concern
**Current Problem**: 1890-line app.py with mixed concerns
```python
# In advance_orb/app.py
@app.get("/api/strategies/advanceorb")
@app.post("/api/orders/place")
@app.get("/api/portfolio/funds")
@app.post("/api/broker/connect")
# ... everything in one file!
```

**Proposed Structure**:
```
api/
├── __init__.py
├── v1/
│   ├── __init__.py
│   ├── strategies.py            # Strategy endpoints
│   ├── orders.py                # Order placement
│   ├── portfolio.py             # Holdings + P&L
│   ├── broker.py                # Broker connection
│   ├── market.py                # Live ticks
│   └── admin.py                 # Manual fetch, etc.
├── dependencies.py              # Shared auth + validation
└── errors.py                    # Unified error handling
```

**Benefits**:
- ✅ Each route file ~100-150 lines (readable)
- ✅ Namespace clearly organized
- ✅ Easy to add v2 later

---

### 2.2 Consistent Error Handling
**Proposed**:
```python
# api/errors.py
class StrategyError(Exception):
    """Base exception for strategy-related errors."""
    pass

class BrokerError(Exception):
    """Base exception for broker-related errors."""
    pass

class DataError(Exception):
    """Base exception for data fetching errors."""
    pass
```

---

## Phase 3: Frontend Reorganization

### 3.1 Component-Based Architecture
**Current Problem**: All JS in separate files, no component structure
```
js/
├── main.js (260+ lines: DOM, nav, toast, modal, constants)
├── screener.js (260+ lines: budget, filters, strategy logic)
├── portfolio.js (260+ lines: holdings, metrics, table)
├── home.js, strategies.js, backtest.js, settings.js
└── style.css (monolithic)
```

**Proposed Structure**:
```
js/
├── config.ts                    # Strategies, constants
├── core/
│   ├── app.ts                   # App initialization
│   ├── router.ts                # Navigation
│   ├── toast.ts                 # Notifications
│   ├── modal.ts                 # Modal system
│   └── auth.ts                  # Auth checks
├── components/
│   ├── Header.ts                # Top bar
│   ├── Sidebar.ts               # Navigation
│   ├── StatsBox.ts              # Metric card
│   ├── StrategyCard.ts          # Strategy card
│   └── Table.ts                 # Data table
├── pages/
│   ├── Home.ts
│   ├── Screener.ts
│   ├── Portfolio.ts
│   ├── Strategies.ts
│   ├── Backtest.ts
│   └── Settings.ts
├── services/
│   ├── api.ts                   # HTTP client
│   ├── broker.ts                # Broker service
│   ├── portfolio.ts             # Portfolio service
│   └── strategies.ts            # Strategy service
├── utils/
│   ├── formatting.ts            # Number formatting
│   ├── validators.ts            # Input validation
│   └── constants.ts             # Global constants
└── styles/
    ├── variables.css            # Design tokens
    ├── components.css           # Component styles
    ├── pages.css                # Page styles
    └── theme.css                # Dark/light theme
```

**Benefits**:
- ✅ Clear separation of concerns
- ✅ Reusable components
- ✅ Easier to test
- ✅ Better maintenance

---

### 3.2 Typed API Layer
**Current**: Direct fetch() calls scattered throughout JS files
**Proposed**:
```typescript
// js/services/api.ts
interface AdvanceOrbResponse {
  strategy: string;
  count: number;
  data: StockRow[];
  columns: string[];
}

async function fetchAdvanceOrb(params: {
  budget: number;
  parts: number;
  // ...
}): Promise<AdvanceOrbResponse> {
  // Centralized API calls
}
```

---

## Phase 4: Testing & Documentation

### 4.1 Improve Test Structure
```
tests/
├── conftest.py                  # Fixtures
├── unit/
│   ├── strategies/
│   │   ├── test_advance_orb.py
│   │   └── test_big_players.py
│   ├── broker/
│   │   ├── test_dhan_auth.py
│   │   └── test_angel_auth.py
│   └── data/
│       └── test_cache.py
├── integration/
│   ├── test_strategy_workflow.py
│   └── test_broker_orders.py
└── e2e/
    └── test_screener_flow.py
```

### 4.2 Add README per Module
```
strategies/advance_orb/README.md
broker/dhan/README.md
api/v1/README.md
js/services/README.md
```

---

## Implementation Roadmap

### Week 1: Backend Phase 1
- [ ] Create `config/` package
- [ ] Consolidate broker code into `broker/` submodules
- [ ] Extract strategy base class

### Week 2: Backend Phase 2
- [ ] Create unified data layer in `strategies/data/`
- [ ] Refactor common.py → shared filters/constants

### Week 3: Backend Phase 3
- [ ] Split `app.py` into `api/v1/` routes
- [ ] Implement error handling layer

### Week 4: Frontend Phase
- [ ] Extract components into `js/components/`
- [ ] Create service layer in `js/services/`
- [ ] Refactor CSS into modular structure

### Week 5: Testing & Documentation
- [ ] Add unit tests for new modules
- [ ] Write README for each major package
- [ ] Create API documentation

---

## File Size Targets (After Optimization)

| File | Current | Target | Improvement |
|------|---------|--------|-------------|
| app.py | 1890 | 300 | Route file per feature |
| main.js | 260 | 100 | Component extraction |
| common.py | 260+ | 150 | Config + data layer split |
| screener.js | 260+ | 120 | Service layer extraction |
| style.css | 800+ | 300 | Modular structure |

---

## Quick Wins (Can do today)

1. **Extract config constants**
   ```python
   # config/defaults.py
   from dataclasses import dataclass
   
   @dataclass
   class StrategyConfig:
       PRICE_MIN: int = 200
       PRICE_MAX: int = 4000
       # Import everywhere instead of duplicate definitions
   ```

2. **Create broker interface**
   ```python
   # broker/base.py
   from abc import ABC, abstractmethod
   
   class Broker(ABC):
       @abstractmethod
       def authenticate(self): pass
       @abstractmethod
       def place_order(self, order): pass
   ```

3. **Consolidate API routes**
   ```python
   # api/v1/__init__.py
   from fastapi import APIRouter
   from .strategies import router as strategies_router
   from .orders import router as orders_router
   # Then: app.include_router(strategies_router)
   ```

---

## Questions for You

1. **TypeScript Migration**: Should frontend migrate to TypeScript? (Better maintainability)
2. **Strategy Priority**: Which strategy (Advance ORB / Big Players) is used most? (Optimize that first)
3. **Broker Priority**: Dhan or Angel One primary? (Consolidate that broker first)
4. **Testing**: How important are e2e tests? (Budget time for this)

