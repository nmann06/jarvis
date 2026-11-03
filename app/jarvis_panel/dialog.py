"""The native connection dialog: host and token, a clear error, and Retry."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from .config import TOKEN_RE, Connection, parse_host


class ConnectionDialog(QDialog):
    def __init__(self, conn: Connection, error: str = "", local: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Jarvis")
        self.setMinimumWidth(460)
        self.setModal(True)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "The Jarvis Panel couldn\u2019t connect. It reads the token from <b>data/panel-token</b> on this machine. "
            "Fix the problem below, then press Retry." if local else
            "The Jarvis Panel couldn\u2019t connect. Enter where Jarvis is running and the panel token "
            "(the contents of <b>data/panel-token</b> on the Jarvis machine). You only need to do this once.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.host = QLineEdit(f"{conn.host}:{conn.port}")
        self.host.setPlaceholderText("hyde  or  100.x.y.z:8767")
        self.token = QLineEdit(conn.token if conn.source == "saved" else "")
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Paste the token here")
        form.addRow("Host", self.host)
        form.addRow("Token", self.token)
        lay.addLayout(form)
        show = QCheckBox("Show token")
        show.toggled.connect(lambda on: self.token.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        lay.addWidget(show)
        self.error = QLabel(error)
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error.setStyleSheet("color: #c0392b;")
        self.error.setVisible(bool(error))
        lay.addWidget(self.error)
        buttons = QDialogButtonBox()
        self.retry = QPushButton("Connect")
        self.retry.setDefault(True)
        self.quit = QPushButton("Quit")
        buttons.addButton(self.retry, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.quit, QDialogButtonBox.ButtonRole.RejectRole)
        lay.addWidget(buttons)
        self.retry.clicked.connect(self._submit)
        self.quit.clicked.connect(self.reject)
        self.host.returnPressed.connect(self._submit)
        self.token.returnPressed.connect(self._submit)
        if local:
            self.host.setEnabled(False)
            self.token.setEnabled(False)
            self.token.setPlaceholderText("Read from data/panel-token")
            self.retry.setText("Retry")
            show.setVisible(False)
        self.local = local
        self.on_submit = None   # set by the controller: callable(Connection)
        self._base = conn

    def show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))
        self.retry.setEnabled(True)
        self.retry.setText("Retry")
        self.host.setEnabled(not self.local)
        self.token.setEnabled(not self.local)

    def _submit(self) -> None:
        if self.local:
            self.error.setVisible(False)
            self.retry.setEnabled(False)
            self.retry.setText("Connecting\u2026")
            if self.on_submit:
                self.on_submit(self._base)
            return
        host, port = parse_host(self.host.text())
        token = self.token.text().strip()
        if not host:
            return self.show_error("Enter the host name or IP address of the Jarvis machine, for example hyde.")
        if not TOKEN_RE.match(token):
            return self.show_error("That doesn’t look like a panel token. Copy the whole line from data/panel-token "
                                   "on the Jarvis machine.")
        self.error.setVisible(False)
        self.retry.setEnabled(False)
        self.retry.setText("Connecting…")
        if self.on_submit:
            self.on_submit(Connection(host, port or self._base.port, token, "saved"))
