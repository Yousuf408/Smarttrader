# broker/config.py

# ─── API URLs ───
DHAN_MARGIN_URL = "https://api.dhan.co/v2/margincalculator"
DHAN_TOKEN_URL = "https://auth.dhan.co/app/generateAccessToken"
DHAN_ORDER_URL = "https://api.dhan.co/v2/orders"
DHAN_FUND_LIMIT_URL = "https://api.dhan.co/v2/fundlimit"
DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

# ─── CREDENTIALS (User will replace these) ───
DHAN_CLIENT_ID = "1102302753"
DHAN_PIN = "786786"
DHAN_TOTP_SECRET = "THWBRO5KI5N7ACJUNY7W3JUDKL4M2LML"

# ─── PROXY SETTINGS ───
DHAN_PROXY_HOST = "151.242.178.149"
DHAN_PROXY_PORT = "50100"
DHAN_PROXY_USERNAME = "yousufshaikh420"
DHAN_PROXY_PASSWORD = "cVTbJi6VVA"

DHAN_PROXY_URL = f"http://{DHAN_PROXY_USERNAME}:{DHAN_PROXY_PASSWORD}@{DHAN_PROXY_HOST}:{DHAN_PROXY_PORT}"
DHAN_PROXIES = {"http": DHAN_PROXY_URL, "https": DHAN_PROXY_URL}
