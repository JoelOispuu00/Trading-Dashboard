import unittest


class TestStrategyOverlaySmoke(unittest.TestCase):
    def test_paint_does_not_crash(self) -> None:
        # Minimal smoke test: instantiate the overlay and call paint() with an exposed rect.
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtWidgets import QApplication, QStyleOptionGraphicsItem

        from app.ui.charts.strategy_overlay import StrategyOverlayRenderer

        app = QApplication.instance() or QApplication([])

        _ = app  # keep reference for the duration of the test

        markers = [
            {"ts": 1000.0, "price": 10.0, "kind": "entry", "side": "LONG"},
            {"ts": 2000.0, "price": 11.0, "kind": "exit", "side": "LONG"},
            {"ts": 3000.0, "price": 9.0, "kind": "entry", "side": "SHORT"},
        ]
        overlay = StrategyOverlayRenderer(markers)

        img = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        painter = QPainter(img)
        try:
            opt = QStyleOptionGraphicsItem()
            opt.exposedRect = QRectF(900.0, 0.0, 2500.0, 100.0)
            overlay.paint(painter, opt, None)
            overlay.paint(painter, opt, None)
        finally:
            painter.end()

        # Should have cached at least one chunk after painting.
        self.assertGreaterEqual(len(getattr(overlay, "_chunk_cache", {}) or {}), 1)


if __name__ == "__main__":
    unittest.main()

