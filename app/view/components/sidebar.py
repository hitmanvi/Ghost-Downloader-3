from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import FluentIconBase, isDarkTheme, qconfig


class Sidebar(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = QLabel(title, self)
        self._scroll = QScrollArea(self)
        self._body = QWidget()
        self._initWidget()
        self._initLayout()
        qconfig.themeChangedFinished.connect(self.refreshTheme)

    def _initWidget(self):
        self.setObjectName("pageSidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self._title.setVisible(bool(self._title.text()))
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._body)
        self._body.setAutoFillBackground(False)
        self._scroll.viewport().setAutoFillBackground(False)
        self.refreshTheme()

    def refreshTheme(self):
        if isDarkTheme():
            bg, line, ink, muted, hover, selected, accent = (
                "#101927", "#344052", "#f1f5fd", "#aab6ca", "#1e2a3b", "#22395d", "#8bb7ff")
        else:
            bg, line, ink, muted, hover, selected, accent = (
                "#f1f4f9", "#dde3ed", "#1b2536", "#5b677c", "#e7edf6", "#dfeaff", "#1c60d7")
        self.setStyleSheet(f"""
            #pageSidebar {{ background: {bg}; border-right: 1px solid {line}; }}
            #pageSidebar QScrollArea {{ background: transparent; border: none; }}
            #pageSidebar QLabel {{ color: {muted}; padding: 4px 12px; }}
            #pageSidebar QPushButton {{ color: {ink}; background: transparent; text-align: left;
                padding: 6px 12px; border: 1px solid transparent; border-radius: 6px; }}
            #pageSidebar QPushButton:hover {{ background: {hover}; }}
            #pageSidebar QPushButton:checked {{ background: {selected}; color: {accent}; }}
            #pageSidebar QPushButton:focus {{ border-color: {accent}; }}
        """)

    def _initLayout(self):
        self._items = QVBoxLayout(self)
        self._items.setContentsMargins(12, 48, 12, 12)
        self._items.setSpacing(4)
        self._items.addWidget(self._title)
        self._items.addWidget(self._scroll, 1)
        self._rows = QVBoxLayout(self._body)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(4)
        self._rows.setAlignment(Qt.AlignmentFlag.AlignTop)

    def addSection(self, title):
        self._rows.addSpacing(12)
        self._rows.addWidget(QLabel(title, self._body))
        rows = QVBoxLayout()
        rows.setSpacing(4)
        self._rows.addLayout(rows)
        return rows

    def add(self, text, icon, *, bottom=False, section=None):
        image = icon.qicon() if isinstance(icon, FluentIconBase) else QIcon(icon)
        button = QPushButton(image, text, self)
        button.setMinimumHeight(36)
        if bottom:
            self._items.addWidget(button)
        elif section is not None:
            section.addWidget(button)
        else:
            self._rows.addWidget(button)
        return button
