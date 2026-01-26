def schema():
    return {
        "id": "adx",
        "name": "ADX",
        "pane": "new",
        "inputs": {
            "length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "color": {"type": "color", "default": "#AB47BC"},
        },
    }


def compute(bars, params, ctx):
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")
    close = ctx.series(bars, "close")
    adx = ctx.adx(high, low, close, int(params.get("length", 14)))
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "adx", "values": adx, "color": params.get("color", "#AB47BC"), "width": 1}
        ],
    }
