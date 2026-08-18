# SmartTrader Pro - Algorithmic Trading Platform

A modern algorithmic trading suite featuring real-time Technical Screener, Advance ORB (Opening Range Breakout), Big Players Strategy, multi-broker connectivity (Dhan & Angel One), and TradingView Scanner integration for NSE Indian equities.

## System Architecture

- **TradingView Scanner (`/tradingview/`)**: Real-time NSE stock filtering by Nifty Total Market constituents (750 stocks), ₹200-₹4000 price bounds, ±2.0% gap limits, >₹4,100 Cr market cap, and 5-Day Median Relative Volume (RELVOL) calculation.
- **Brokers (`/broker/`)**: Multi-broker execution layer with Dhan API and Angel One SmartAPI integration, margin calculators, and holding managers.
- **Strategies**:
  - `Advance ORB (`/advance_orb/`)`: 5-minute opening range breakout strategy with 200 EMA trend filtering.
  - `Big Players (`/bigplayers/`)`: Institutional volume spikes and momentum tracking.
- **Web App & Server**: Node.js/Express server (`server.js`) serving the responsive trading dashboard UI and strategy endpoints on Port 3000.

## Quick Start

1. Install dependencies:
   ```bash
   npm install
   pip install -r requirements.txt
   ```
2. Run development server:
   ```bash
   npm start
   ```
3. Access Dashboard at `http://localhost:3000`.
