def schema():
    return {
        "id": "donchian",
        "name": "Donchian Channels",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 500},
            "color": {"type": "color", "default": "#26A69A"},
            "fill": {"type": "color", "default": "#26A69A33"},
        },
    }


def compute(bars, params, ctx):
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")
    length = int(params.get("length", 20))
    upper = ctx.highest(high, length)
    lower = ctx.lowest(low, length)
    color = params.get("color", "#26A69A")
    return {
        "pane": "price",
        "bands": [
            {"type": "band", "id": "donchian", "upper": upper, "lower": lower, "fill": params.get("fill", "#26A69A33"),
             "edge_color": color, "edge_width": 1}
        ],
    }
