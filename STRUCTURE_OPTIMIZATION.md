# Structure Optimization & Architecture Guide

## Architecture Overview
- **Advance ORB (`/advance_orb/`)**: 5-min & 15-min Opening Range Breakout strategy with 200 EMA and Inside Bar filters.
- **Big Players (`/bigplayers/`)**: Support bounce, institutional volume surge, and breakout tracker.
- **Broker (`/broker/`)**: DhanHQ and Angel One SmartAPI connectors for orders, holdings, margins, and WebSockets.
- **TradingView (`/tradingview/`)**: Custom filters and OHLC feed adapters.
- **Frontend (`/`, `/js/`, `/style.css`)**: Live UI dashboard with real-time screener, auto-buy execution, and portfolio monitoring.
