# Kolopovstrategy_light (fixed)

Worker-only deployment for Railway. Includes:
- `positions_guard.py` — main loop (H1 tick or on-demand run)
- `core/` helpers: env loader, indicators/predict, time utils, trade log, train_model stub
- `position_manager.py` — Bybit v5 order placement via pybit
- `.github/workflows/` — CI and deploy workflows
- `requirements.txt` — deps

## Quick start
1) Create `.env` in project root:
```
BYBIT_API_KEY=...
BYBIT_SECRET_KEY=...
DOMAIN=bybit
PROXY_URL=
PAIRS=TON/USDT,ETH/USDT
AMOUNT=5
LEVERAGE=5
RISK_FRACTION=0.05
RECV_WINDOW=10000
DRY_RUN=true
```
2) Install deps: `pip install -r requirements.txt`
3) Run once: `python positions_guard.py`

## Railway
- Service name: `worker`
- Start command: `python positions_guard.py`
- Put env vars from `.env` into Railway Variables (do not commit .env)
