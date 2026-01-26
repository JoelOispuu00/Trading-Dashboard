def schema():
    return {
        "id": "rsi",
        "name": "RSI",
        "pane": "new",
        "inputs": {
            "length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "color": {"type": "color", "default": "#42A5F5"},
            "ob": {"type": "float", "default": 70.0, "min": 0.0, "max": 100.0, "step": 0.1},
            "os": {"type": "float", "default": 30.0, "min": 0.0, "max": 100.0, "step": 0.1},
        },
    }


def compute(bars, params, ctx):
    close = ctx.series(bars, "close")
    rsi = ctx.rsi(close, int(params.get("length", 14)))
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "rsi", "values": rsi, "color": params.get("color", "#42A5F5"), "width": 1}
        ],
        "levels": [
            {"value": params.get("ob", 70.0), "color": "#EF5350", "style": "dash", "width": 1},
            {"value": params.get("os", 30.0), "color": "#00C853", "style": "dash", "width": 1},
            {"value": 50.0, "color": "#9E9E9E99", "style": "dot", "width": 1},
        ],
    }
