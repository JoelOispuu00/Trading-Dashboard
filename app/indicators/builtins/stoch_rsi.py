import numpy as np


def schema():
    return {
        "id": "stoch_rsi",
        "name": "Stoch RSI",
        "pane": "new",
        "inputs": {
            "rsi_length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "stoch_length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "smooth_k": {"type": "int", "default": 3, "min": 1, "max": 50},
            "smooth_d": {"type": "int", "default": 3, "min": 1, "max": 50},
            "k_color": {"type": "color", "default": "#42A5F5"},
            "d_color": {"type": "color", "default": "#FFA726"},
        },
    }


def compute(bars, params, ctx):
    close = ctx.series(bars, "close")
    rsi = ctx.rsi(close, int(params.get("rsi_length", 14)))
    length = int(params.get("stoch_length", 14))
    lowest = ctx.lowest(rsi, length)
    highest = ctx.highest(rsi, length)
    denom = highest - lowest
    stoch = np.where(denom != 0, (rsi - lowest) / denom * 100.0, np.nan)
    k = ctx.sma(stoch, int(params.get("smooth_k", 3)))
    d = ctx.sma(k, int(params.get("smooth_d", 3)))
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "k", "values": k, "color": params.get("k_color", "#42A5F5"), "width": 1},
            {"type": "line", "id": "d", "values": d, "color": params.get("d_color", "#FFA726"), "width": 1},
        ],
        "levels": [
            {"value": 80.0, "color": "#EF5350", "style": "dash", "width": 1},
            {"value": 20.0, "color": "#00C853", "style": "dash", "width": 1},
        ],
    }
