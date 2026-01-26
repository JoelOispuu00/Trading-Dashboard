def schema():
    return {
        "id": "ema",
        "name": "EMA",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 500},
            "source": {"type": "select", "default": "close", "options": ["open", "high", "low", "close"]},
            "color": {"type": "color", "default": "#00C853"},
        },
    }


def compute(bars, params, ctx):
    src = ctx.series(bars, params.get("source", "close"))
    values = ctx.ema(src, int(params.get("length", 20)))
    return {
        "pane": "price",
        "series": [
            {"type": "line", "id": "ema", "values": values, "color": params.get("color", "#00C853"), "width": 1}
        ],
    }
