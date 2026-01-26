def schema():
    return {
        "id": "bb",
        "name": "Bollinger Bands",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 500},
            "mult": {"type": "float", "default": 2.0, "min": 0.1, "max": 10.0, "step": 0.1},
            "color": {"type": "color", "default": "#42A5F5"},
            "fill": {"type": "color", "default": "#42A5F533"},
        },
    }


def compute(bars, params, ctx):
    close = ctx.series(bars, "close")
    upper, basis, lower = ctx.bb(close, int(params.get("length", 20)), float(params.get("mult", 2.0)))
    color = params.get("color", "#42A5F5")
    return {
        "pane": "price",
        "series": [
            {"type": "line", "id": "basis", "values": basis, "color": color, "width": 1},
        ],
        "bands": [
            {
                "type": "band",
                "id": "bb",
                "upper": upper,
                "lower": lower,
                "fill": params.get("fill", "#42A5F533"),
                "edge_color": color,
                "edge_width": 1,
            }
        ],
    }
