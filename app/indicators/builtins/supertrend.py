def schema():
    return {
        "id": "supertrend",
        "name": "Supertrend",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 10, "min": 1, "max": 200},
            "mult": {"type": "float", "default": 3.0, "min": 0.1, "max": 10.0, "step": 0.1},
            "color": {"type": "color", "default": "#00C853"},
        },
    }


def compute(bars, params, ctx):
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")
    close = ctx.series(bars, "close")
    line = ctx.supertrend(high, low, close, int(params.get("length", 10)), float(params.get("mult", 3.0)))
    return {
        "pane": "price",
        "series": [
            {"type": "line", "id": "supertrend", "values": line, "color": params.get("color", "#00C853"), "width": 1}
        ],
    }
