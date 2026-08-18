# System Architecture & Directory Structure

## 1. Directory Layout (Root Page Folders)

```text
├── style.css                  # Single common global stylesheet for all pages
│
├── screener/                  # Screener Page Folder
│   ├── screener.html          # Screener table, filters, auto-buy UI
│   └── screener.js            # Screener browser controller
│
├── home/                      # Home Dashboard Page Folder
│   ├── home.html              # Home overview & quick stats UI
│   └── home.js                # Home dashboard controller
│
├── advance_orb/               # Advance ORB Strategy Folder
│   ├── orb.html               # Advance ORB UI
│   ├── orb.js                 # Advance ORB controller
│   ├── candle_recorder.py     # Python candle recorder & breakout engine
│   ├── nifty_ohlc.py          # Nifty OHLC streamer
│   ├── common.py              # Common math, indicators & 200 EMA
│   └── auth_routes.py         # Strategy auth routes
│
├── bigplayers/                # Big Players Strategy Folder
│   ├── bigplayers.html        # Big Players UI
│   ├── bigplayers.js          # Big Players controller
│   ├── strategy.py            # Big Players volume surge strategy logic
│   └── routes.py              # Big Players routes
│
├── portfolio/                 # Portfolio & Positions Folder
│   ├── portfolio.html         # Live positions & holdings UI
│   └── portfolio.js           # Portfolio controller
│
├── strategies/                # Strategies Manager Folder
│   ├── strategies.html        # Strategy cards & summary UI
│   └── strategies.js          # Strategy manager controller
│
├── settings/                  # Settings Folder
│   ├── settings.html          # Broker API credentials UI
│   └── settings.js            # Settings controller
│
├── backtest/                  # Backtest Folder
│   ├── backtest.html          # Backtest configuration UI
│   └── backtest.js            # Backtest controller
│
├── testing/                   # Testing Lab Folder
│   ├── testing.html           # Diagnostics & testing UI
│   └── testing.js             # Diagnostics controller
│
├── tradingview/               # TradingView Data Engine
│   ├── scanner.js             # TradingView Scanner API & 5-day median RELVOL
│   ├── tv_ohlc_ws.py          # Python OHLC WebSocket client
│   ├── tv_stocks_filters.py   # Technical filters
│   └── nifty_total_market.json# 750 Constituent symbols list
│
├── broker/                    # Multi-Broker Gateway
│   ├── app.py                 # Unified broker gateway
│   ├── dhan_orders.py         # Dhan order placement
│   ├── dhan_holdings.py       # Dhan portfolio holdings
│   ├── dhan_ws.py             # Dhan WebSocket feed
│   ├── angel_orders.py        # Angel One SmartAPI orders
│   ├── angel_holdings.py      # Angel One portfolio holdings
│   ├── angel_ws.py            # Angel One WebSocket feed
│   ├── angel_margin_calculator.py # Margin calculator
│   └── quantity_calculator.py # Dynamic lot size calculator
│
├── server.js                  # Express Server & Strategy API Gateway
├── index.html                 # Main App Shell
└── package.json
```
