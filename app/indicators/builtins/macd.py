def schema():
    return {
        "id": "macd",
        "name": "MACD",
        "pane": "new",
        "inputs": {
            "fast": {"type": "int", "default": 12, "min": 1, "max": 200},
            "slow": {"type": "int", "default": 26, "min": 1, "max": 200},
            "signal": {"type": "int", "default": 9, "min": 1, "max": 200},
            "macd_color": {"type": "color", "default": "#42A5F5"},
            "signal_color": {"type": "color", "default": "#FFA726"},
            "hist_up": {"type": "color", "default": "#00C853"},
            "hist_down": {"type": "color", "default": "#EF5350"},
        },
    }


def compute(bars, params, ctx):
    close = ctx.series(bars, "close")
    macd_line, signal, hist = ctx.macd(
        close,
        int(params.get("fast", 12)),
        int(params.get("slow", 26)),
        int(params.get("signal", 9)),
    )
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "macd", "values": macd_line, "color": params.get("macd_color", "#42A5F5"), "width": 1},
            {"type": "line", "id": "signal", "values": signal, "color": params.get("signal_color", "#FFA726"), "width": 1},
        ],
        "hist": [
            {
                "type": "hist",
                "id": "hist",
                "values": hist,
                "color_up": params.get("hist_up", "#00C853"),
                "color_down": params.get("hist_down", "#EF5350"),
            }
        ],
    }
