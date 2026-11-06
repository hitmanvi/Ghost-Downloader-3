from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget
from qfluentwidgets import FluentIconBase, Theme


class Sidebar(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = QLabel(title, self)
        self._initWidget()
        self._initLayout()

    def _initWidget(self):
        self.setObjectName("pageSidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet("""
            QWidget#pageSidebar { background: #143852; }
            QWidget#pageSidebar QLabel { color: #b0cad9; padding: 4px 8px; }
            QWidget#pageSidebar QPushButton {
                background: transparent; color: #dfedf3; text-align: left;
                padding: 6px 8px; border: none; border-left: 3px solid transparent;
                border-radius: 6px;
            }
            QWidget#pageSidebar QPushButton:hover { background: #20485f; }
            QWidget#pageSidebar QPushButton:checked {
                background: #25546b; color: white; border-left-color: #78e0c8;
            }
            QWidget#pageSidebar QPushButton:focus { border-bottom: 1px solid #78e0c8; }
        """)

    def _initLayout(self):
        self._items = QVBoxLayout(self)
        self._items.setContentsMargins(12, 52, 12, 12)
        self._items.setSpacing(4)
        self._items.addWidget(self._title)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(4)
        self._items.addLayout(self._rows)
        self._items.addStretch()

    def add(self, text, icon, *, bottom=False):
        image = icon.icon(Theme.DARK) if isinstance(icon, FluentIconBase) else QIcon(icon)
        button = QPushButton(image, text, self)
        button.setMinimumHeight(36)
        if bottom:
            self._items.addWidget(button)
        else:
            self._rows.addWidget(button)
        return button
