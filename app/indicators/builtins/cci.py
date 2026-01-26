def schema():
    return {
        "id": "cci",
        "name": "CCI",
        "pane": "new",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 200},
            "color": {"type": "color", "default": "#26C6DA"},
        },
    }


def compute(bars, params, ctx):
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")
    close = ctx.series(bars, "close")
    cci = ctx.cci(high, low, close, int(params.get("length", 20)))
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "cci", "values": cci, "color": params.get("color", "#26C6DA"), "width": 1}
        ],
        "levels": [
            {"value": 100.0, "color": "#EF5350", "style": "dash", "width": 1},
            {"value": -100.0, "color": "#00C853", "style": "dash", "width": 1},
            {"value": 0.0, "color": "#9E9E9E99", "style": "dot", "width": 1},
        ],
    }
