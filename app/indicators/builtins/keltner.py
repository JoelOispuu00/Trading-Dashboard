def schema():
    return {
        "id": "keltner",
        "name": "Keltner Channels",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 500},
            "mult": {"type": "float", "default": 2.0, "min": 0.1, "max": 10.0, "step": 0.1},
            "color": {"type": "color", "default": "#7E57C2"},
            "fill": {"type": "color", "default": "#7E57C233"},
        },
    }


def compute(bars, params, ctx):
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")
    close = ctx.series(bars, "close")
    upper, basis, lower = ctx.keltner(
        high, low, close, int(params.get("length", 20)), float(params.get("mult", 2.0))
    )
    color = params.get("color", "#7E57C2")
    return {
        "pane": "price",
        "series": [
            {"type": "line", "id": "basis", "values": basis, "color": color, "width": 1},
        ],
        "bands": [
            {"type": "band", "id": "keltner", "upper": upper, "lower": lower, "fill": params.get("fill", "#7E57C233"),
             "edge_color": color, "edge_width": 1}
        ],
    }
