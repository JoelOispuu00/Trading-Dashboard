def schema():
    return {
        "id": "psar",
        "name": "Parabolic SAR",
        "pane": "price",
        "inputs": {
            "accel": {"type": "float", "default": 0.02, "min": 0.001, "max": 1.0, "step": 0.01},
            "max_accel": {"type": "float", "default": 0.2, "min": 0.01, "max": 1.0, "step": 0.01},
            "color": {"type": "color", "default": "#FFA726"},
        },
    }


def compute(bars, params, ctx):
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")
    sar = ctx.psar(high, low, float(params.get("accel", 0.02)), float(params.get("max_accel", 0.2)))
    return {
        "pane": "price",
        "series": [
            {"type": "scatter", "id": "psar", "values": sar, "color": params.get("color", "#FFA726"), "width": 1}
        ],
    }
