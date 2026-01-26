def schema():
    return {
        "id": "dmi",
        "name": "DMI",
        "pane": "new",
        "inputs": {
            "length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "plus_color": {"type": "color", "default": "#00C853"},
            "minus_color": {"type": "color", "default": "#EF5350"},
        },
    }


def compute(bars, params, ctx):
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")
    close = ctx.series(bars, "close")
    plus_di, minus_di = ctx.dmi(high, low, close, int(params.get("length", 14)))
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "plus_di", "values": plus_di, "color": params.get("plus_color", "#00C853"), "width": 1},
            {"type": "line", "id": "minus_di", "values": minus_di, "color": params.get("minus_color", "#EF5350"), "width": 1},
        ],
    }
