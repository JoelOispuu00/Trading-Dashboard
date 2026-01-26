def schema():
    return {
        "id": "momentum",
        "name": "Momentum",
        "pane": "new",
        "inputs": {
            "length": {"type": "int", "default": 10, "min": 1, "max": 200},
            "color": {"type": "color", "default": "#8E24AA"},
        },
    }


def compute(bars, params, ctx):
    close = ctx.series(bars, "close")
    mom = ctx.momentum(close, int(params.get("length", 10)))
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "momentum", "values": mom, "color": params.get("color", "#8E24AA"), "width": 1}
        ],
        "levels": [
            {"value": 0.0, "color": "#9E9E9E99", "style": "dot", "width": 1},
        ],
    }
