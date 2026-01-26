def schema():
    return {
        "id": "rma",
        "name": "RMA",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 500},
            "source": {"type": "select", "default": "close", "options": ["open", "high", "low", "close"]},
            "color": {"type": "color", "default": "#FDD835"},
        },
    }


def compute(bars, params, ctx):
    src = ctx.series(bars, params.get("source", "close"))
    values = ctx.rma(src, int(params.get("length", 20)))
    return {
        "pane": "price",
        "series": [
            {"type": "line", "id": "rma", "values": values, "color": params.get("color", "#FDD835"), "width": 1}
        ],
    }
