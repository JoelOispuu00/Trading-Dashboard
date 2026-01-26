def schema():
    return {
        "id": "vwma",
        "name": "VWMA",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 500},
            "source": {"type": "select", "default": "close", "options": ["open", "high", "low", "close"]},
            "color": {"type": "color", "default": "#FF7043"},
        },
    }


def compute(bars, params, ctx):
    src = ctx.series(bars, params.get("source", "close"))
    values = ctx.vwma(src, int(params.get("length", 20)))
    return {
        "pane": "price",
        "series": [
            {"type": "line", "id": "vwma", "values": values, "color": params.get("color", "#FF7043"), "width": 1}
        ],
    }
