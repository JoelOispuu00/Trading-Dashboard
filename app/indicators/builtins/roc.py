def schema():
    return {
        "id": "roc",
        "name": "ROC",
        "pane": "new",
        "inputs": {
            "length": {"type": "int", "default": 12, "min": 1, "max": 200},
            "color": {"type": "color", "default": "#FF7043"},
        },
    }


def compute(bars, params, ctx):
    close = ctx.series(bars, "close")
    roc = ctx.roc(close, int(params.get("length", 12)))
    return {
        "pane": "new",
        "series": [
            {"type": "line", "id": "roc", "values": roc, "color": params.get("color", "#FF7043"), "width": 1}
        ],
        "levels": [
            {"value": 0.0, "color": "#9E9E9E99", "style": "dot", "width": 1},
        ],
    }
