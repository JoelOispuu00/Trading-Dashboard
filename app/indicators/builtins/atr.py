def schema():
    return {
        "id": "atr",
        "name": "ATR",
        "pane": "new",
        "inputs": {
            "length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "color": {"type": "color", "default": "#FFA726"},
        },
    }


def compute(bars, params, ctx):
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")
    close = ctx.series(bars, "close")
    atr = ctx.atr(high, low, close, int(params.get("length", 14)))
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "atr", "values": atr, "color": params.get("color", "#FFA726"), "width": 1}
        ],
    }
