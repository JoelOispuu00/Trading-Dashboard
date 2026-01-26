def schema():
    return {
        "id": "hma",
        "name": "HMA",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 2, "max": 500},
            "source": {"type": "select", "default": "close", "options": ["open", "high", "low", "close"]},
            "color": {"type": "color", "default": "#00BCD4"},
        },
    }


def compute(bars, params, ctx):
    src = ctx.series(bars, params.get("source", "close"))
    values = ctx.hma(src, int(params.get("length", 20)))
    return {
        "pane": "price",
        "series": [
            {"type": "line", "id": "hma", "values": values, "color": params.get("color", "#00BCD4"), "width": 1}
        ],
    }
