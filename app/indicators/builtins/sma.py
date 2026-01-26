def schema():
    return {
        "id": "sma",
        "name": "SMA",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 500},
            "source": {"type": "select", "default": "close", "options": ["open", "high", "low", "close"]},
            "color": {"type": "color", "default": "#42A5F5"},
        },
    }


def compute(bars, params, ctx):
    src = ctx.series(bars, params.get("source", "close"))
    values = ctx.sma(src, int(params.get("length", 20)))
    return {
        "pane": "price",
        "series": [
            {"type": "line", "id": "sma", "values": values, "color": params.get("color", "#42A5F5"), "width": 1}
        ],
    }
