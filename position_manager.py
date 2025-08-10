from typing import Dict, Any, Optional
from pybit.unified_trading import HTTP  # type: ignore
from core.env_loader import normalize_symbol

def make_session(api_key: str, api_secret: str, domain: str = "bybit") -> HTTP:
    return HTTP(api_key=api_key, api_secret=api_secret, domain=domain)

def set_leverage(session: HTTP, symbol_pair: str, leverage: int, category: str = "linear") -> Dict[str, Any]:
    symbol = normalize_symbol(symbol_pair)  # 'TON/USDT' -> 'TONUSDT'
    resp = session.set_leverage(category=category, symbol=symbol,
                                buyLeverage=str(leverage), sellLeverage=str(leverage))
    # 110043 = leverage not modified — это ОК
    if isinstance(resp, dict) and resp.get("retCode") == 110043:
        return {"retCode": 0, "retMsg": "leverage already set"}
    return resp 

def open_position(
    session: HTTP,
    symbol_pair: str,
    direction: str,
    qty: float,
    order_type: str = "Market",
    price: Optional[float] = None,
    time_in_force: str = "PostOnly",
    reduce_only: bool = False,
    category: str = "linear",
    recv_window: int = 15000,
) -> Dict[str, Any]:
    symbol = normalize_symbol(symbol_pair)
    side = "Buy" if direction.strip().lower() == "long" else "Sell"

    params = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": order_type,
        "qty": str(qty),
        "reduceOnly": reduce_only,
        "recvWindow": recv_window,
    }
    if order_type.lower() == "limit":
        if price is None:
            raise ValueError("Limit order requires price.")
        params["price"] = str(price)
        params["timeInForce"] = time_in_force
    else:
        # Market: force immediate-or-cancel
        params["timeInForce"] = "ImmediateOrCancel"

    return {"request": params, "response": session.place_order(**params)}
