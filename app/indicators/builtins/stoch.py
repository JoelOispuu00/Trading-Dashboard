def schema():
    return {
        "id": "stoch",
        "name": "Stochastic",
        "pane": "new",
        "inputs": {
            "k_length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "d_length": {"type": "int", "default": 3, "min": 1, "max": 50},
            "k_color": {"type": "color", "default": "#42A5F5"},
            "d_color": {"type": "color", "default": "#FFA726"},
            "ob": {"type": "float", "default": 80.0, "min": 0.0, "max": 100.0, "step": 0.1},
            "os": {"type": "float", "default": 20.0, "min": 0.0, "max": 100.0, "step": 0.1},
        },
    }


def compute(bars, params, ctx):
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")
    close = ctx.series(bars, "close")
    k, d = ctx.stoch(high, low, close, int(params.get("k_length", 14)), int(params.get("d_length", 3)))
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "k", "values": k, "color": params.get("k_color", "#42A5F5"), "width": 1},
            {"type": "line", "id": "d", "values": d, "color": params.get("d_color", "#FFA726"), "width": 1},
        ],
        "levels": [
            {"value": params.get("ob", 80.0), "color": "#EF5350", "style": "dash", "width": 1},
            {"value": params.get("os", 20.0), "color": "#00C853", "style": "dash", "width": 1},
        ],
    }
