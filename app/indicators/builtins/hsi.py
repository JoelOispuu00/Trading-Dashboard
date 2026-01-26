import numpy as np


def schema():
    return {
        "id": "hsi",
        "name": "HSI",
        "pane": "new",
        "inputs": {
            "rsi_length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "stoch_length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "smooth_k": {"type": "int", "default": 3, "min": 1, "max": 50},
            "hsi_smooth1": {"type": "int", "default": 5, "min": 1, "max": 200},
            "hsi_smooth2": {"type": "int", "default": 3, "min": 1, "max": 200},
            "use_ema": {"type": "bool", "default": True},
            "ob_level": {"type": "float", "default": 88.6, "min": 0, "max": 100, "step": 0.1},
            "os_level": {"type": "float", "default": 11.3, "min": 0, "max": 100, "step": 0.1},
            "show_hist": {"type": "bool", "default": True},
            "show_signal": {"type": "bool", "default": True},
            "show_bg": {"type": "bool", "default": True},
            "hsi_color": {"type": "color", "default": "#42A5F5"},
            "signal_color": {"type": "color", "default": "#FFA726"},
            "hist_color": {"type": "color", "default": "#FFFFFF99"},
            "ob_color": {"type": "color", "default": "#FF6B6B"},
            "os_color": {"type": "color", "default": "#4ECDC4"},
            "bg_overbought": {"type": "color", "default": "#FF00001A"},
            "bg_oversold": {"type": "color", "default": "#00C8531A"},
        },
    }


def compute(bars, params, ctx):
    close = ctx.series(bars, "close")
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")

    rsi_val = ctx.rsi(close, int(params.get("rsi_length", 14)))
    k_raw, _ = ctx.stoch(high, low, close, int(params.get("stoch_length", 14)), int(params.get("smooth_k", 3)))
    k = ctx.sma(k_raw, int(params.get("smooth_k", 3)))
    hsi_raw = (rsi_val * k) / 100.0

    smooth1 = int(params.get("hsi_smooth1", 5))
    smooth2 = int(params.get("hsi_smooth2", 3))
    if params.get("use_ema", True):
        hsi_fast = ctx.ema(hsi_raw, smooth1)
        hsi = ctx.ema(hsi_fast, smooth2)
        signal = ctx.ema(hsi, max(1, smooth2 * 2))
    else:
        hsi_fast = ctx.sma(hsi_raw, smooth1)
        hsi = ctx.sma(hsi_fast, smooth2)
        signal = ctx.sma(hsi, max(1, smooth2 * 2))
    hist = hsi - signal

    series = [
        {"type": "line", "id": "hsi", "values": hsi, "color": params.get("hsi_color", "#42A5F5"), "width": 2}
    ]
    if params.get("show_signal", True):
        series.append({"type": "line", "id": "signal", "values": signal, "color": params.get("signal_color", "#FFA726"), "width": 1})

    hist_items = []
    if params.get("show_hist", True):
        hist_items.append({
            "type": "hist",
            "id": "hist",
            "values": hist,
            "color_up": params.get("hist_color", "#FFFFFF99"),
            "color_down": params.get("hist_color", "#FFFFFF99"),
        })

    regions = []
    if params.get("show_bg", True):
        regions.append({"start_ts": None, "end_ts": None, "color": params.get("bg_overbought", "#FF00001A"), "when": "hsi>ob"})
        regions.append({"start_ts": None, "end_ts": None, "color": params.get("bg_oversold", "#00C8531A"), "when": "hsi<os"})

    return {
        "pane": "new",
        "series": series,
        "hist": hist_items,
        "levels": [
            {"value": params.get("ob_level", 88.6), "color": params.get("ob_color", "#FF6B6B"), "style": "dash", "width": 1},
            {"value": params.get("os_level", 11.3), "color": params.get("os_color", "#4ECDC4"), "style": "dash", "width": 1},
            {"value": 50.0, "color": "#9E9E9E99", "style": "dash", "width": 1},
        ],
        "regions": regions,
    }
