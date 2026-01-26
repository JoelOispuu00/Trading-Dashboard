def schema():
    return {
        "id": "wma",
        "name": "WMA",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 500},
            "source": {"type": "select", "default": "close", "options": ["open", "high", "low", "close"]},
            "color": {"type": "color", "default": "#8E24AA"},
        },
    }


def compute(bars, params, ctx):
    src = ctx.series(bars, params.get("source", "close"))
    values = ctx.wma(src, int(params.get("length", 20)))
    return {
        "pane": "price",
        "series": [
            {"type": "line", "id": "wma", "values": values, "color": params.get("color", "#8E24AA"), "width": 1}
        ],
    }
