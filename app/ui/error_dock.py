import time

from PyQt6.QtWidgets import QDockWidget, QTextEdit


class ErrorDock(QDockWidget):
    def __init__(self) -> None:
        super().__init__('Errors')
        self.setObjectName('ErrorDock')
        self._last_message: str = ""
        self._last_message_at: float = 0.0

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText('Indicator errors will appear here.')
        self.setWidget(self.text)

    def append_error(self, message: str) -> None:
        # Avoid spamming identical errors (e.g., repeated missing-range failures) while the UI retries.
        now = time.monotonic()
        if message == self._last_message and (now - self._last_message_at) < 2.0:
            return
        self._last_message = message
        self._last_message_at = now
        self.text.append(message)
