# Quick Start & Performance Optimization

## 1. High-Performance TradingView Scanner
The TradingView Scanner fetches real-time market data across 750 Nifty Total Market equities with sub-second latency:
- In-memory 15-second response caching (`TV_CACHE_TTL_MS = 15000`)
- Batch asynchronous prefetching of 5-Day Historical Volume medians from Yahoo Finance
- Fast in-memory Set lookup for constituent filtering

## 2. Broker Integrations
- **Dhan**: Seamless WebSocket quote streaming, order placement, and holdings portfolio sync.
- **Angel One**: SmartAPI order manager, margin calculator, and token-based authentication.

## 3. Strategies Available
- **Advance ORB**: Real-time 5-minute candle opening breakout tracker with target & stop-loss calculations.
- **Big Players**: Institutional money flow tracker with Relative Volume threshold analysis.
