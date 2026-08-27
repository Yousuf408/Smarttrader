# SmartTrader · Indian Stock Screener & Algo Trading Platform

A real-time Indian stock market screener and automated trading platform with direct broker integration for **Angel One** (SmartAPI) and **DhanHQ**.

---

## 📁 Current Project Architecture

```
smarttrader/
├── index.html                  # Master Shell (Sidebar, Topbar, Modals, Script loader)
├── style.css                   # Global styling, themes, animations, modern responsive layout
├── main.js                     # Central App Controller (Routing, LiveFeedManager, UI State, SSE ticks)
├── server.js                   # Node.js Express & WebSocket Backend Proxy (Broker APIs, Live Feed, Orders)
│
├── auth/
│   ├── auth.html               # Authentication / Broker Login Modal Component
│   └── auth.js                 # Angel One (SmartAPI TOTP) & Dhan authentication handlers
│
├── home/
│   ├── home.html               # Home Dashboard Page (Stats, Market Overview, Quick Actions)
│   └── home.js                 # Home dynamic data loader & live trade summaries
│
├── screener/
│   ├── screener.html           # Real-Time Screener UI (Filters, Strategy selection, Auto-Buy)
│   └── screener.js             # Screener calculations (Advance ORB, SmartMoney, Big Players, Multi-TF)
│
├── portfolio/
│   ├── portfolio.html          # Holdings, Positions, and Active Orders UI
│   └── portfolio.js            # Live broker portfolio sync, P&L calculations, square-off
│
├── settings/
│   ├── settings.html           # Broker credentials & Risk Management UI
│   └── settings.js             # API key storage, stop-loss / target rules, capital limits
│
├── broker/                     # Python Broker Execution & Margin Calculation Modules
│   ├── app.py                  # Flask bridge for broker calculations
│   ├── angel_orders.py         # Angel One order placement & modification
│   ├── angel_holdings.py       # Angel One live holdings & portfolio sync
│   ├── angel_margin_calculator.py
│   ├── dhan_orders.py          # DhanHQ order placement
│   ├── dhan_holdings.py        # DhanHQ live holdings & portfolio sync
│   └── quantity_calculator.py  # Position sizing & risk-to-reward calculation
│
├── package.json                # Node.js dependencies & scripts
├── metadata.json               # Platform runtime configurations
└── README.md                   # Project documentation
```

---

## 🚀 Key Features

### 1. 📊 Real-Time Screeners & Strategies
- **Advance ORB (Opening Range Breakout)** with 5m / 15m candle breakout detection.
- **SmartMoney & Big Players Tracking** based on delivery volume spikes and institutional footprints.
- **Multi-Timeframe Filtering** (1m, 5m, 15m, Daily) with real-time SSE tick streaming.
- **Auto-Buy Execution Engine** with pre-configured Stop-Loss and Target multiples.

### 2. 🔐 Dual Broker Integration
- **Angel One (SmartAPI)**: Automated TOTP authentication, SmartStream WebSocket live market feed, margin calculator, and fast order routing.
- **DhanHQ**: Client ID + Access Token API integration for order placement, positions, and live holdings sync.

### 3. 💼 Portfolio & Risk Management
- Real-time P&L tracking with automatic live market tick updates.
- Position sizing and capital allocation safeguards (Max Capital per Trade, Daily Drawdown Limits).
- One-click Position Square-Off and Order Modification.

### 4. ⚡ Live Market Streamer
- Backend server-sent events (`/api/market/live-ticks/stream`) & WebSocket bridge for NIFTY 50, BANKNIFTY, and custom stock watchlists.

---

## 💻 Tech Stack

- **Frontend**: Vanilla JavaScript (ES6+), HTML5, Modern CSS3 with Light/Dark Mode.
- **Backend Server**: Node.js (`Express.js`, `ws` WebSockets, `axios`, `cookie-parser`).
- **Broker Microservices**: Python (`requests`, `pyotp`, `Flask`).
- **Live Feed**: SmartAPI WebSocket & Server-Sent Events (SSE).

---

## 📖 Quick Start

### 1. Install Dependencies
```bash
npm install
```

### 2. Start the Server
```bash
npm start
```
The application will launch on `http://localhost:3000`.

---

## ⚙️ Configuration & Credentials

Open the **Settings** tab in the web UI to configure:
1. **Angel One**: API Key, Client Code, PIN, TOTP Secret Key.
2. **Dhan**: Client ID, Access Token.
3. **Risk Rules**: Max allocation per trade, default Stop-Loss %, Target %.
