# TradeAlgo Pro · Trading Platform

A premium, modular stock trading and screener platform built with vanilla HTML, CSS, and JavaScript. Features multiple trading strategies, real-time screener, backtesting, and portfolio management.

## 📁 Project Structure

```
smarttrader/
├── index.html          # Main HTML file (all pages)
├── style.css           # All CSS styles
├── js/
│   ├── main.js         # Core navigation, DOM, Toast, Modal, Constants (STRATEGIES)
│   ├── home.js         # Homepage logic
│   ├── strategies.js   # Strategies page & modal save
│   ├── screener.js     # Screener with strategy dropdown & auto-buy toggle
│   ├── portfolio.js    # Portfolio & holdings logic
│   ├── backtest.js     # Backtest runner logic
│   └── settings.js     # Settings & broker configuration
├── images/             # (Optional) Image assets folder
└── README.md           # This file
```

## 🚀 Features

### 📊 Pages
1. **Home** - Dashboard with stats, recent trades, market overview
2. **Strategies** - View, manage, and start/stop trading strategies
3. **Screener** - Stock screener with multiple strategy presets
   - Strategy dropdown (Advance ORB, SmartMoney, Big Players)
   - Auto-Buy toggle for hands-free trading
   - Dynamic table columns based on selected strategy
4. **Portfolio** - Holdings, P&L, and portfolio statistics
5. **Backtest** - Strategy backtesting with detailed metrics
6. **Settings** - Broker connection and risk management configuration

### ⚡ Core Features
- **Global Navigation** - Seamless page switching
- **Toast Notifications** - User feedback system
- **Modal System** - Strategy creation/editing
- **Strategy Configurations** - Pre-configured trading strategies with data
- **Auto-Buy System** - Toggle to automatically execute orders
- **Responsive Design** - Works on desktop and mobile devices

## 💻 Tech Stack

- **HTML5** - Semantic markup
- **CSS3** - Glass morphism, gradients, animations
- **Vanilla JavaScript (ES6)** - No frameworks, lightweight & fast
- **Modular Architecture** - Organized code split into functional modules

## 📖 Getting Started

### 1. **Clone or Download**
```bash
git clone https://github.com/yourusername/smarttrader.git
cd smarttrader
```

### 2. **Open in Browser**
Simply open `index.html` in your web browser. No build process needed!

```bash
# On Mac
open index.html

# On Windows
start index.html

# On Linux
xdg-open index.html
```

### 3. **Local Server (Optional)**
For better development experience, use a local server:

```bash
# Using Python 3
python -m http.server 8000

# Using Python 2
python -m SimpleHTTPServer 8000

# Using Node.js (http-server)
npx http-server
```

Then open: `http://localhost:8000`

## 🗂️ Module Breakdown

### `js/main.js` ⭐
- **DOM References** - Centralized DOM element access
- **Global State** - `autoBuyEnabled` flag
- **Navigation System** - `navigateTo(pageId)` function
- **Toast System** - `showToast()`, `hideToast()`
- **Modal System** - `openModal()`, `closeModal()`
- **STRATEGIES Constant** - Pre-configured strategy data with 3 strategies:
  - Advance ORB (6 stocks, 72% win rate)
  - SmartMoney (5 stocks, 65% win rate)
  - Big Players (6 stocks, 58% win rate)

### `js/home.js`
- `loadHome()` - Populate dashboard stats, recent trades, market overview

### `js/strategies.js`
- `loadStrategies()` - Display strategy cards and performance summary
- `saveStrategy()` - Save strategy modifications to modal

### `js/screener.js`
- `onStrategyChange()` - Update table columns and data when strategy changes
- `toggleAutoBuyMode()` - Enable/disable auto-buy trading
- `updatePlaceOrderButtons()` - Disable buttons when auto-buy is active
- `autoBuyAllStocks()` - Execute bulk orders
- `runScreener()` - Fetch and update stock data
- `placeOrder(symbol)` - Place individual order

### `js/portfolio.js`
- `loadPortfolio()` - Display holdings and portfolio statistics

### `js/backtest.js`
- `runBacktest()` - Run backtest simulation with loading animation

### `js/settings.js`
- `toggleBrokerFields()` - Show/hide broker-specific form fields

## 🎨 Design System

### Color Palette
- **Primary Gradient**: `#6C5CE7` → `#A29BFE` (Blue-Violet)
- **Success**: `#00b894` (Green)
- **Danger**: `#e17055` (Red)
- **Warning**: `#fdcb6e` (Yellow)

### Typography
- **Font**: Inter, -apple-system, sans-serif
- **Sizes**: 11px to 30px for hierarchy
- **Weights**: 300-800 for visual emphasis

### Components
- **Cards** - `stat-box`, `strategy-card`, `panel-glass`
- **Buttons** - Primary, Success, Danger, Outline, Small
- **Tables** - `table-modern` with hover effects
- **Badges** - Buy, Sell, Hold states
- **Toggle Switch** - Custom design with animations

## 📱 Responsive Breakpoints

- **Desktop** - Full layout
- **1024px** - Tablet adjustments
- **768px** - Mobile adjustments (stack grids)
- **480px** - Small mobile (single column)

## 🔧 Customization

### Adding a New Strategy
1. Open `js/main.js`
2. Add to `STRATEGIES` object:
```javascript
newstrategy: {
    id: 'newstrategy',
    name: 'New Strategy Name',
    icon: '📌',
    entryRule: 'Entry Rule Description',
    risk: '2%',
    columns: ['Symbol', 'Price', 'Action'],
    data: [
        { symbol: 'STOCK1', price: '100', ... }
    ]
}
```
3. Add dropdown option in `index.html`:
```html
<option value="newstrategy">📌 New Strategy Name</option>
```

### Changing Colors
Edit CSS variables in `style.css`:
```css
:root {
    --brand-start: #6C5CE7;
    --brand-end: #A29BFE;
    --color-success: #00b894;
    /* ... etc */
}
```

### Adding Pages
1. Create new `<div id="page-pagename" class="page">` in `index.html`
2. Add navigation link: `<a data-page="pagename">Page Name</a>`
3. Create `js/pagename.js` with `loadPagename()` function
4. Add script tag in `index.html`: `<script src="js/pagename.js"></script>`
5. Call in `navigateTo()` function in `main.js`

## 📊 Strategy Data Structure

Each strategy contains:
```javascript
{
    id: 'unique-id',
    name: 'Display Name',
    icon: 'emoji',
    entryRule: 'Entry description',
    risk: '2%',
    columns: ['Col1', 'Col2', 'Col3'],
    data: [
        {
            symbol: 'STOCK',
            price: '100',
            change: '+2%',
            // ... additional fields matching columns
        }
    ]
}
```

## 🐛 Troubleshooting

### Styles not loading?
- Check that `style.css` is in the root directory
- Verify relative paths in `index.html`

### JavaScript errors?
- Open browser DevTools (F12)
- Check console for error messages
- Verify all `.js` files load in correct order in `index.html`

### Script connections not working?
- Ensure script tags use correct paths: `<script src="js/main.js"></script>`
- Check that files are in `js/` folder
- Verify file names match exactly (case-sensitive on Linux/Mac)

## 🚀 Deployment

### Deploy on GitHub Pages
1. Push code to GitHub repository
2. Go to repo Settings → Pages
3. Select `main` branch as source
4. Your site will be live at `https://yourusername.github.io/smarttrader`

### Deploy on Other Platforms
- **Netlify**: Drag & drop `smarttrader` folder
- **Vercel**: Connect GitHub repo
- **Firebase Hosting**: Run `firebase deploy`
- **Any Static Hosting**: Upload all files maintaining folder structure

## 📝 License

MIT License - Feel free to use for personal or commercial projects.

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## 📧 Support

For issues and questions, open an GitHub issue or contact the maintainer.

---

**Made with ❤️ for traders** | Built 100% vanilla - No dependencies, no bloat. Pure JavaScript. 🚀
