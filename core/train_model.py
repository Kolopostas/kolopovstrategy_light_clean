# Placeholder to keep imports stable; replace with real training when read# core/train_model.py
import os
from pathlib import Path
import pandas as pd
import numpy as np

try:
    import xgboost as xgb
except Exception:
    xgb = None
from joblib import dump

from core.predict import (
    fetch_ohlcv_df, compute_ema, compute_rsi, compute_macd, compute_atr
)

def _make_features(df: pd.DataFrame) -> pd.DataFrame:
    price = df["close"]
    ema50 = compute_ema(price, 50)
    rsi14 = compute_rsi(price, 14)
    macd, macd_sig = compute_macd(price)
    atr14 = compute_atr(df, 14)

    X = pd.DataFrame({
        "rsi14": rsi14,
        "ema_ratio": price / (ema50.replace(0, np.nan)),
        "macd": macd,
        "macd_signal": macd_sig,
        "atr14": atr14.fillna(0),
        "volume": df["volume"],
    }).dropna()

    # простая цель: next_close > close  → 1 (LONG), иначе 0 (SHORT)
    y = (df["close"].shift(-1).loc[X.index] > df["close"].loc[X.index]).astype(int)
    return X, y

def _symbol_no_slash(symbol: str) -> str:
    return symbol.replace("/", "").upper()

def train_one(symbol: str, proxy_url: str = "", timeframe="1h", limit=2000, test_frac=0.2, seed=42, out_dir="models") -> str:
    Path(out_dir).mkdir(exist_ok=True)
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else {}
    df = fetch_ohlcv_df(symbol, timeframe=timeframe, limit=limit, proxies=proxies)
    X, y = _make_features(df)
    if len(X) < 200:
        raise RuntimeError(f"Недостаточно данных для {symbol}: {len(X)} строк")

    # train/test split по времени
    split = int(len(X) * (1 - test_frac))
    Xtr, Xte = X.iloc[:split], X.iloc[split:]
    ytr, yte = y.iloc[:split], y.iloc[split:]

    if xgb is None:
        # запасной вариант: логистическая регрессия
        from sklearn.linear_model import LogisticRegression
        model = LogisticRegression(max_iter=200, random_state=seed, n_jobs=None)
        model.fit(Xtr, ytr)
    else:
        model = xgb.XGBClassifier(
            n_estimators=400, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0,
            random_state=seed, n_jobs=4, tree_method="hist"
        )
        model.fit(Xtr, ytr, eval_set=[(Xte, yte)], verbose=False)

    path = os.path.join(out_dir, f"model_{_symbol_no_slash(symbol)}.pkl")
    dump(model, path)
    return path

def train_models(pairs, proxy_url=""):
    out = []
    for p in pairs:
        try:
            path = train_one(p, proxy_url=proxy_url)
            out.append((p, path, "OK"))
        except Exception as e:
            out.append((p, "", f"ERR: {e}"))
    return out