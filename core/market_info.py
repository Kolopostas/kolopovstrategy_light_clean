# core/market_info.py
from typing import Dict, Any
from functools import lru_cache
from pybit.unified_trading import HTTP  # type: ignore

@lru_cache(maxsize=256)
def get_instrument_info(session: HTTP, symbol_noslash: str, category: str = "linear") -> Dict[str, Any]:
    """
    Кэшируем информацию об инструменте (лот/тик/минималки).
    symbol_noslash: 'ETHUSDT', 'BTCUSDT', ...
    """
    resp = session.get_instruments_info(category=category, symbol=symbol_noslash)
    if resp.get("retCode") != 0:
        raise RuntimeError(f"instruments-info error: {resp.get('retCode')} {resp.get('retMsg')}")
    lst = resp.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError(f"instruments-info empty for {symbol_noslash}")
    return lst[0]

def _step_floor(x: float, step: float) -> float:
    # безопасное округление вниз к шагу (для qty/price)
    if step <= 0:
        return x
    k = int(x / step + 1e-12)
    return k * step

def _step_ceil(x: float, step: float) -> float:
    if step <= 0:
        return x
    k = int(x / step - 1e-12)
    v = (k if k * step >= x else k + 1) * step
    # страховка от аккумулирующей ошибки
    if v < x:
        v += step
    return v

def adjust_qty_price(info: Dict[str, Any], qty: float, price: float) -> (float, float):
    """
    Приводим qty/price к шагам и минималкам инструмента, проверяем «ноционал».
    Возвращаем (qty_adj, price_adj).
    """
    lot = info["lotSizeFilter"]
    qstep = float(lot["qtyStep"])
    min_qty = float(lot.get("minOrderQty", qstep))

    pf = info["priceFilter"]
    tick = float(pf["tickSize"])

    min_amt = float(info.get("minOrderAmt", 0) or 0)

    qty_adj = max(_step_floor(qty, qstep), min_qty)
    price_adj = _step_floor(price, tick)

    # проверим минимальный ноционал
    notional = price_adj * qty_adj
    if min_amt and notional < min_amt:
        qty_adj = _step_ceil(min_amt / max(price_adj, 1e-9), qstep)

    return qty_adj, price_adj

def get_available_usdt(session: HTTP) -> float:
    """
    Доступный баланс USDT (для UTA). Если используешь суб‑аккаунты/кошельки, адаптируй.
    """
    resp = session.get_wallet_balance(accountType="UNIFIED")
    if resp.get("retCode") != 0:
        raise RuntimeError(f"wallet-balance error: {resp.get('retCode')} {resp.get('retMsg')}")
    for coin in resp.get("result", {}).get("list", [])[0].get("coin", []):
        if coin.get("coin") == "USDT":
            return float(coin.get("availableToWithdraw", 0))
    return 0.0