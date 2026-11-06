from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QTextCursor, QTextOption
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QHBoxLayout, QVBoxLayout
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    BodyLabel, FluentIcon, FluentStyleSheet, FluentTitleBar,
    IndeterminateProgressBar, InfoBar, InfoBarIcon, InfoBarPosition,
    MessageBoxBase, PushButton, SubtitleLabel, TransparentPushButton, TransparentTogglePushButton,
    isDarkTheme, qconfig, setCustomStyleSheet,
)

from app.config.cfg import cfg
from app.view.cards.draft_cards import FailedDraftCard
from app.view.components.card_groups import DraftCardGroup
from app.view.components.control_sizes import CONTROL_HEIGHT, CONTROL_ICON_SIZE
from app.view.components.editors import AutoSizingEdit, addRecentFolder
from app.view.components.option_cards import OutputFolderCard, SubworkerCountCard
from app.i18n import toLocalizedError

if TYPE_CHECKING:
    from app.models.task import Task, TaskError
    from app.services.task_draft import TaskDraft


class StandaloneWrapper(FramelessDialog):

    def __init__(self, dialog: TaskDraftDialog):
        super().__init__()
        self._dialog = dialog
        self.contentLayout = QVBoxLayout(self)

        self._initWidget()
        self._initLayout()

    def _initWidget(self) -> None:
        self.setResizeEnabled(False)
        titleBar = FluentTitleBar(self)
        self.setTitleBar(titleBar)
        self.titleBar.maxBtn.hide()
        self.titleBar.iconLabel.hide()
        self.titleBar.setDoubleClickEnabled(False)
        self.titleBar.setFixedHeight(30)
        self.setWindowTitle(self._dialog.tr("添加任务"))
        FluentStyleSheet.DIALOG.apply(self)

    def _initLayout(self) -> None:
        self.contentLayout.setContentsMargins(0, 30, 0, 0)
        self.contentLayout.setSpacing(0)

    def setContent(self, widget) -> None:
        self.contentLayout.addWidget(widget)

    def takeContent(self, widget) -> None:
        self.contentLayout.removeWidget(widget)

    def closeEvent(self, event) -> None:
        event.ignore()
        self._dialog.reject()


class TaskDraftDialog(MessageBoxBase):

    def __init__(self, draft: TaskDraft, featureService, categoryService, parent=None):
        super().__init__(parent)
        self._draft = draft
        self._featureService = featureService
        self._categoryService = categoryService
        self._parseTimer = QTimer(self, singleShot=True)
        self._standaloneWrapper = StandaloneWrapper(self)
        self.destroyed.connect(self._standaloneWrapper.deleteLater)
        self._isStandalone = False
        self._dragPos = QPoint()
        self.titleLabel = SubtitleLabel(self.tr("添加任务"), self)
        self.urlEdit = AutoSizingEdit(self, minimumVisibleLines=3, maximumVisibleLines=5)
        self.progressBar = IndeterminateProgressBar(self)
        self.draftGroup = DraftCardGroup(self)
        self.folderCard = OutputFolderCard(self)
        self.threadCard = SubworkerCountCard(self)
        self.advancedButton = TransparentTogglePushButton(FluentIcon.SETTING, self.tr("高级选项"), self.buttonGroup)
        self.laterButton = PushButton(self.tr("稍后下载"), self.buttonGroup)
        self.batchButton = TransparentPushButton(FluentIcon.COPY, self.tr("批量添加"), self)
        self.importButton = TransparentPushButton(FluentIcon.FOLDER_ADD, self.tr("导入文件"), self)
        self.headerLayout = QHBoxLayout()

        self._initWidget()
        self._initLayout()
        self._bind()

    @property
    def isActive(self) -> bool:
        return self.isVisible() or (self._isStandalone and self._standaloneWrapper.isVisible())

    def _initWidget(self) -> None:
        self.hide()
        self.widget.setFixedWidth(640)
        self.urlEdit.setPlaceholderText(self.tr("粘贴下载链接，每行一个"))
        self.urlEdit.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.progressBar.hide()
        self._fileTypes = self._featureService.fileTypes()
        self.importButton.setVisible(bool(self._fileTypes))

        self.draftGroup.hide()
        self.threadCard.hide()
        self.yesButton.setText(self.tr("下载"))
        self.cancelButton.setText(self.tr("取消"))
        self.buttonGroup.setFixedHeight(56)
        for button in (self.batchButton, self.importButton, self.advancedButton,
                       self.cancelButton, self.laterButton, self.yesButton):
            button.setFixedHeight(CONTROL_HEIGHT)
            button.setIconSize(QSize(CONTROL_ICON_SIZE, CONTROL_ICON_SIZE))
        self.urlEdit.setAccessibleName(self.tr("下载链接"))
        self.folderCard.layout().setContentsMargins(0, 4, 0, 4)
        self.threadCard.layout().setContentsMargins(0, 4, 0, 4)
        setCustomStyleSheet(self.urlEdit, "PlainTextEdit { background: #f5f8fd; }",
                            "PlainTextEdit { background: #1e2a3b; }")
        setCustomStyleSheet(self.yesButton, "PrimaryPushButton { background: #185ed9; color: white; }",
                            "PrimaryPushButton { background: #2465d9; color: white; }")
        self._refreshTheme()

    def _initLayout(self) -> None:
        self.viewLayout.setContentsMargins(24, 18, 24, 12)
        self.viewLayout.setSpacing(8)
        self.viewLayout.addWidget(self.titleLabel)
        self.headerLayout.setSpacing(6)
        self.headerLayout.addWidget(BodyLabel(self.tr("下载链接"), self))
        self.headerLayout.addStretch(1)
        self.headerLayout.addWidget(self.batchButton)
        self.headerLayout.addWidget(self.importButton)
        self.viewLayout.addLayout(self.headerLayout)
        self.viewLayout.addWidget(self.urlEdit)
        self.viewLayout.addWidget(self.progressBar)
        self.viewLayout.addWidget(self.draftGroup)
        self.viewLayout.addWidget(self.folderCard)
        self.viewLayout.addWidget(self.threadCard)
        self.buttonLayout.removeWidget(self.yesButton)
        self.buttonLayout.removeWidget(self.cancelButton)
        self.buttonLayout.setContentsMargins(20, 8, 24, 8)
        self.buttonLayout.setSpacing(8)
        self.buttonLayout.addWidget(self.advancedButton)
        self.buttonLayout.addStretch()
        self.buttonLayout.addWidget(self.cancelButton)
        self.buttonLayout.addWidget(self.laterButton)
        self.buttonLayout.addWidget(self.yesButton)

    def _bind(self) -> None:
        self._parseTimer.setInterval(1000)
        self._parseTimer.timeout.connect(self._onParseNeeded)
        self.urlEdit.textChanged.connect(self._parseTimer.start)

        self._draft.parsingBusyChanged.connect(self.progressBar.setVisible)
        self._draft.parseSucceeded.connect(self._onParseSucceeded)
        self._draft.parseFailed.connect(self._onParseFailed)
        self.draftGroup.refreshRequested.connect(self._draft.refreshFailed)
        self._draft.itemsChanged.connect(self._onItemsChanged)
        self._draft.itemsCleared.connect(self._onCleared)

        self.laterButton.clicked.connect(self._onLaterClicked)
        self.advancedButton.toggled.connect(self.threadCard.setVisible)
        qconfig.themeChangedFinished.connect(self._refreshTheme)
        self.batchButton.clicked.connect(self._onBatchClicked)
        self.importButton.clicked.connect(self._onImportClicked)

    def showStandalone(self) -> None:
        from app.platform.desktop import raiseWindow

        if not self.isActive:
            self._setDefaultOptions()

        if self._isStandalone and self._standaloneWrapper.isVisible():
            raiseWindow(self._standaloneWrapper)
            self.urlEdit.setFocus()
            self.urlEdit.moveCursor(QTextCursor.MoveOperation.End)
            return

        if self.isVisible() and not self._isStandalone:
            self.setGraphicsEffect(None)
            self.widget.setGraphicsEffect(None)
            QDialog.done(self, QDialog.DialogCode.Rejected)

        if not self._isStandalone:
            self._toStandalone()

        raiseWindow(self._standaloneWrapper)

        if not self.urlEdit.toPlainText().strip():
            urls = self._clipboardUrls()
            if urls:
                self.urlEdit.appendPlainText("\n".join(urls))
        self.urlEdit.setFocus()
        self.urlEdit.moveCursor(QTextCursor.MoveOperation.End)

    def showMask(self) -> int:
        if not self.isActive:
            self._setDefaultOptions()
        if self._isStandalone:
            self._toMask()

        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(0, 0, parent.width(), parent.height())
            self.windowMask.resize(self.size())
        self.setShadowEffect(60, (0, 10), QColor(0, 0, 0, 50))
        self.setMaskColor(QColor(0, 0, 0, 76))

        if not self.urlEdit.toPlainText().strip():
            urls = self._clipboardUrls()
            if urls:
                self.urlEdit.appendPlainText("\n".join(urls))
        self.urlEdit.setFocus()
        self.urlEdit.moveCursor(QTextCursor.MoveOperation.End)

        return self.exec()

    def addUrls(self, urls: list[str]) -> None:
        if not urls:
            return
        existing = set(self._urls())
        toAdd = [stripped for u in urls if (stripped := u.strip()) and stripped not in existing]
        if not toAdd:
            return
        self.urlEdit.appendPlainText("\n".join(toAdd))
        self._parseTimer.stop()
        self._onParseNeeded()

    def addParsedTasks(self, tasks: list[Task], error: TaskError | None = None) -> None:
        if not tasks:
            return
        newUrls = self._draft.addParsedTasks(tasks)
        if newUrls:
            self.urlEdit.appendPlainText("\n".join(newUrls))
        self._parseTimer.stop()
        if error is not None:
            QTimer.singleShot(0, lambda: self._showAddError(tasks, error))

    def done(self, code: int, autoStart=True) -> None:
        if code == QDialog.DialogCode.Accepted:
            self._draft.confirm(autoStart=autoStart)
            if self._draft.urls():
                self.urlEdit.setPlainText("\n".join(self._draft.urls()))
                return
            addRecentFolder(str(self.folderCard.options()["outputFolder"]))
        else:
            self._draft.clear()

        self.urlEdit.clear()
        self._parseTimer.stop()
        self.draftGroup.clear()
        self.draftGroup.updateStats(0, 0, 0)

        if self._isStandalone:
            self._standaloneWrapper.hide()
        else:
            super().done(code)

    def validate(self) -> bool:
        self._parseTimer.stop()
        self._onParseNeeded()
        return self._draft.canConfirm()

    def eventFilter(self, obj, event: QEvent):
        if obj is not self.windowMask:
            return super().eventFilter(obj, event)

        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._dragPos = event.pos()
            return True

        if event.type() == QEvent.Type.MouseMove and not self._dragPos.isNull():
            window = self.window()
            if window.isMaximized():
                window.showNormal()
            position = window.pos() + event.pos() - self._dragPos
            position.setX(max(0, position.x()))
            position.setY(max(0, position.y()))
            window.move(position)
            return True

        if event.type() == QEvent.Type.MouseButtonRelease:
            self._dragPos = QPoint()

        return super().eventFilter(obj, event)

    def _toStandalone(self) -> None:
        self._hBoxLayout.removeWidget(self.widget)
        self._standaloneWrapper.setContent(self.widget)
        self.widget.show()
        self._isStandalone = True
        self._refreshTheme()

    def _toMask(self) -> None:
        self._standaloneWrapper.hide()
        self._standaloneWrapper.takeContent(self.widget)
        self._hBoxLayout.addWidget(self.widget, 1, Qt.AlignmentFlag.AlignCenter)
        self.widget.show()
        self._isStandalone = False
        self._refreshTheme()

    def _refreshTheme(self) -> None:
        surface, tint, line = ("#172131", "#1e2a3b", "#344052") if isDarkTheme() else (
            "#ffffff", "#f5f8fd", "#dde3ed")
        radius = 0 if self._isStandalone else 8
        self.widget.setStyleSheet(f"""
            #centerWidget {{ background: {surface}; border: 1px solid {line}; border-radius: {radius}px; }}
            #buttonGroup {{ background: {tint}; border: none; border-top: 1px solid {line};
                border-bottom-left-radius: {radius}px; border-bottom-right-radius: {radius}px; }}
        """)

    def _setDefaultOptions(self) -> None:
        self.folderCard.reset()
        self.threadCard.reset()
        self.advancedButton.setChecked(False)

    def _onLaterClicked(self) -> None:
        if self.validate():
            self.done(QDialog.DialogCode.Accepted, autoStart=False)

    def _onParseNeeded(self) -> None:
        self._draft.setBaseOptions(self.folderCard.options() | self.threadCard.options())
        self._draft.setUrls(self._urls())

    def _onParseSucceeded(self, url: str, task: Task) -> None:
        card = self._featureService.draftCard(task, self.draftGroup)
        card.categoryPicked.connect(lambda cid: self._draft.setUrlCategory(url, cid))
        card.editRequested.connect(lambda u=url: self._onEditRequested(u))
        self.draftGroup.addCard(url, card)
        self._refreshStats()

    def _onEditRequested(self, url: str) -> None:
        from app.view.dialogs.edit_task import DraftEditDialog

        task = self._draft.taskByUrl(url)
        if task is None:
            return
        dialog = DraftEditDialog(task, self._featureService.optionCards(task, self.window()), self.window())
        dialog.exec()
        dialog.deleteLater()

    def _onParseFailed(self, url: str, error: TaskError) -> None:
        if url not in self._urls():
            self.urlEdit.appendPlainText(url)
        card = FailedDraftCard(url, toLocalizedError(error), self.draftGroup)
        card.refreshRequested.connect(lambda: self._draft.refresh(url))
        self.draftGroup.addCard(url, card)
        self._refreshStats()

    def _showAddError(self, tasks: list[Task], error: TaskError) -> None:
        bar = InfoBar(
            icon=InfoBarIcon.ERROR,
            title=self.tr("无法添加任务"),
            content=toLocalizedError(error),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            duration=-1,
            position=InfoBarPosition.BOTTOM_RIGHT,
            parent=self,
        )
        if any(task.outputFolder.is_relative_to(cfg.downloadFolder.value) for task in tasks):
            button = PushButton(self.tr("更改默认文件夹"))
            button.clicked.connect(lambda: self._onChangeDefaultFolderClicked(bar))
            bar.addWidget(button)
        bar.show()

    def _onChangeDefaultFolderClicked(self, bar: InfoBar) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr("选择默认下载文件夹"), str(Path.home()))
        if not folder:
            return
        cfg.set(cfg.downloadFolder, folder)
        addRecentFolder(folder)
        self._setDefaultOptions()
        bar.close()

    def _onItemsChanged(self) -> None:
        self.draftGroup.setUrls([item.url for item in self._draft.items() if item.task or item.error])
        self._refreshStats()

    def _onCleared(self) -> None:
        self.draftGroup.clear()
        self.draftGroup.updateStats(0, 0, 0)

    def _refreshStats(self) -> None:
        tasks = [self._draft.taskByUrl(url) for url in self._draft.urls()]
        successCount = sum(1 for t in tasks if t is not None)
        totalSize = sum(t.fileSize for t in tasks if t is not None and t.fileSize > 0)
        self.draftGroup.updateStats(successCount, self._draft.failCount(), totalSize)

    def _urls(self) -> list[str]:
        text = self.urlEdit.toPlainText()
        if not text:
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _clipboardUrls(self) -> list[str]:
        text = QApplication.clipboard().text()
        return [url for line in text.splitlines()
                if (url := line.strip()) and self._featureService.match(url)]

    def _onBatchClicked(self) -> None:
        from app.view.dialogs.batch_url import BatchUrlDialog
        parent = self._standaloneWrapper if self._isStandalone else self.window()
        dialog = BatchUrlDialog(parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.addUrls(dialog.urls())
        dialog.deleteLater()

    def _onImportClicked(self) -> None:
        globs = [f"*{ext}" for ft in self._fileTypes for ext in ft.extensions]
        nameFilters = [self.tr("所有可导入文件 ({0})").format(" ".join(globs))]
        nameFilters += [
            f"{ft.displayName} ({' '.join(f'*{ext}' for ext in ft.extensions)})"
            for ft in self._fileTypes
        ]
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr("导入文件"), "", ";;".join(nameFilters))
        if paths:
            self.addUrls([Path(p).as_uri() for p in paths])
