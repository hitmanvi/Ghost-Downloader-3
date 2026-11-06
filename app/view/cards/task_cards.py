from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileInfo, QPoint, QRect, QRectF, Signal, Qt, QT_TRANSLATE_NOOP as N
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter
from PySide6.QtWidgets import QFileIconProvider, QHBoxLayout, QLabel, QVBoxLayout, QApplication, QWidget, QSizePolicy
from qfluentwidgets import (
    Action, CardWidget, CheckBox, FluentIcon, Theme,
    IndeterminateProgressBar, PushButton, ProgressBar, ProgressRing,
    RoundMenu, ToolButton, ToolTipFilter, TransparentToolButton,
    isDarkTheme, setCustomStyleSheet, themeColor,
)

from app.config.cfg import cfg
from app.format import toReadableSize, toReadableTime
from app.checksum import ALGORITHMS, COMMON_ALGORITHMS
from app.models.task import TaskStatus, SpecialFileSize
from app.platform.desktop import openFile, revealInFolder
from app.platform.filesystem import isExisting, isFolder
from app.view.components.labels import ElidedLabel, IconBodyLabel, IconStrongBodyLabel
from app.view.components.category_settings import toCategoryName
from app.i18n import toLocalizedError

if TYPE_CHECKING:
    from app.models.task import Task
    from qfluentwidgets import MessageBoxBase



@dataclass(frozen=True)
class FieldSpec:
    name: str
    icon: FluentIcon
    formats: dict[TaskStatus | None, Callable[[Task, int, int], str | None]]


@dataclass(frozen=True)
class ButtonState:
    icon: FluentIcon | None = None
    visible: bool = True
    enabled: bool = True


BUTTON_DEFAULT = ButtonState()


@dataclass(frozen=True)
class ButtonSpec:
    name: str
    icon: FluentIcon
    tooltip: str
    buttonType: type = ToolButton
    states: dict[TaskStatus | None, Callable] | None = None
    default: ButtonState = field(default=BUTTON_DEFAULT)


def toEtaText(task: Task, speed: int, received: int) -> str | None:
    if task.fileSize > 0 and speed > 0:
        return toReadableTime(int((task.fileSize - received) / speed))
    return None


def toSizeText(task: Task, speed: int, received: int) -> str | None:
    if task.fileSize > 0:
        return toReadableSize(task.fileSize)
    return toReadableSize(received) if received > 0 else None


class TaskProgressBar(ProgressBar):
    def barColor(self):
        if self.isPaused() or self.isError():
            return super().barColor()
        gradient = QLinearGradient(0, 0, max(1, self.width() * self.val / 100), 0)
        gradient.setColorAt(0, QColor("#5394ff" if isDarkTheme() else "#367cf8"))
        gradient.setColorAt(1, QColor("#43cff4" if isDarkTheme() else "#13b9eb"))
        return gradient


SPEED_FIELD = FieldSpec("speed", FluentIcon.SPEED_HIGH, {
    TaskStatus.RUNNING: lambda t, s, r: f"{toReadableSize(s)}/s",
})
ETA_FIELD = FieldSpec("eta", FluentIcon.STOP_WATCH, {TaskStatus.RUNNING: toEtaText})
SIZE_FIELD = FieldSpec("size", FluentIcon.LIBRARY, {None: toSizeText})

TOGGLE_BUTTON = ButtonSpec("toggle", FluentIcon.PLAY, N("TaskCard", "暂停/继续"), PushButton, states={
    TaskStatus.RUNNING: lambda t: ButtonState(icon=FluentIcon.PAUSE, enabled=t.canPause),
    TaskStatus.COMPLETED: lambda t: ButtonState(
        icon=FluentIcon.PAUSE if t.isSeeding else None,
        enabled=t.canSeed and (not t.hasOutputFile or isExisting(t.outputPath))),
})

SELECT_FILES_BUTTON = ButtonSpec("selectFiles", FluentIcon.LIBRARY, N("TaskCard", "选择文件"),
    states={None: lambda t: ButtonState(visible=bool(t.files) and len(t.files) > 1)})

RING_SIZE = 16
STOP_SIZE = 6


class ChecksumButton(ToolButton):
    def __init__(self, icon, parent=None):
        super().__init__(parent)
        self.setIcon(icon)
        self.ring = ProgressRing(self, useAni=False)
        self._initWidget()
        self._initLayout()

    def _initWidget(self) -> None:
        self.ring.setFixedSize(RING_SIZE, RING_SIZE)
        self.ring.setStrokeWidth(2)
        self.ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ring.hide()

    def _initLayout(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignCenter)

    def setProgress(self, progress: int | None) -> None:
        self.ring.setVisible(progress is not None)
        self.ring.setValue(progress or 0)
        self.update()

    def _drawIcon(self, icon, painter, rect, state=QIcon.Off):
        if self.ring.isHidden():
            return super()._drawIcon(icon, painter, rect, state)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255) if isDarkTheme() else QColor(0, 0, 0))
        center = rect.center()
        painter.drawRoundedRect(QRectF(center.x() - STOP_SIZE / 2, center.y() - STOP_SIZE / 2, STOP_SIZE, STOP_SIZE), 1.5, 1.5)


CHECKSUM_BUTTON = ButtonSpec("checksum", FluentIcon.FINGERPRINT, N("TaskCard", "计算校验值"), ChecksumButton, states={
    None: lambda t: ButtonState(visible=not (bool(t.files) and len(t.files) > 1), enabled=False),
    TaskStatus.COMPLETED: lambda t: ButtonState(
        visible=not (bool(t.files) and len(t.files) > 1),
        enabled=t.hasOutputFile and isExisting(t.outputPath)),
})

OPEN_FILE_BUTTON = ButtonSpec("openFile", FluentIcon.LINK, N("TaskCard", "打开文件"), PushButton, states={
    None: lambda t: ButtonState(enabled=False),
    TaskStatus.COMPLETED: lambda t: ButtonState(
        enabled=not t.hasOutputFile or isExisting(t.outputPath)),
})

OPEN_FOLDER_BUTTON = ButtonSpec("openFolder", FluentIcon.FOLDER, N("TaskCard", "打开文件夹"))
DELETE_BUTTON = ButtonSpec("delete", FluentIcon.CLOSE, N("TaskCard", "删除"), TransparentToolButton)


class TaskCard(CardWidget):
    ROW_HEIGHT = 80
    NAME_STRETCH = 13
    STATUS_STRETCH = 10
    SIZE_WIDTH = 80
    ACTION_WIDTH = 136
    COLUMN_MARGIN = 12
    COLUMN_GAP = 12
    selectionChanged = Signal(bool, bool)
    dragRequested = Signal(str)

    infoFields: list[FieldSpec] = [SPEED_FIELD, ETA_FIELD, SIZE_FIELD]
    nameFormats: dict[TaskStatus, Callable] = {}
    buttons: list[ButtonSpec] = [
        TOGGLE_BUTTON, SELECT_FILES_BUTTON, CHECKSUM_BUTTON,
        OPEN_FILE_BUTTON, OPEN_FOLDER_BUTTON, DELETE_BUTTON,
    ]

    speedLabel: ElidedLabel
    etaLabel: ElidedLabel
    sizeLabel: ElidedLabel
    toggleButton: PushButton
    selectFilesButton: ToolButton
    checksumButton: ChecksumButton
    openFileButton: PushButton
    openFolderButton: ToolButton
    deleteButton: TransparentToolButton

    def __init__(self, task: Task, taskService, featureService, categoryService, parent=None):
        super().__init__(parent)
        self._task = task
        self._taskService = taskService
        self._featureService = featureService
        self._categoryService = categoryService
        self._isSelectionMode = False
        self._isFileMissing = False
        self._isDragPending = False
        self._hasDragged = False
        self._dragStartPos = QPoint()
        self._lastStatus: TaskStatus | None = None

        self.setFixedHeight(self.ROW_HEIGHT)

        self.checkBox = CheckBox(self)
        self.iconLabel = QLabel(self)
        self.iconLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nameLabel = IconStrongBodyLabel(task.name, self)
        self.statusLabel = ElidedLabel(parent=self)
        self.statusLabel.hide()
        self.reasonLabel = ElidedLabel(parent=self)
        self.reasonLabel.hide()

        for f in self.infoFields:
            label = (ElidedLabel(parent=self) if f.name in {"size", "eta", "speed"}
                     else IconBodyLabel("", f.icon, self))
            setattr(self, f"{f.name}Label", label)

        self.moreButton = TransparentToolButton(FluentIcon.MORE, self)
        self.moreButton.setAccessibleName(self.tr("更多任务操作"))
        self.secondaryButtons = QWidget(self)
        self.secondaryButtons.hide()
        for spec in self.buttons:
            parent = self if spec.name in {"toggle", "openFile", "checksum"} else self.secondaryButtons
            if issubclass(spec.buttonType, PushButton):
                button = spec.buttonType(spec.icon, "", parent)
            else:
                button = spec.buttonType(spec.icon, parent)
            if parent is self.secondaryButtons:
                button.show()
            setattr(self, f"{spec.name}Button", button)

        self.progressBar = self._createProgressBar()

        if isinstance(self.progressBar, (ProgressBar, IndeterminateProgressBar)):
            self.progressBar.setCustomBarColor(QColor("#367cf8"), QColor("#5394ff"))
        self._initWidget()
        self._initLayout()
        self._bind()
        self._refreshIcon()

    @property
    def task(self) -> Task:
        return self._task

    def _createProgressBar(self) -> QWidget:
        if self._task.fileSize in {SpecialFileSize.UNKNOWN, SpecialFileSize.NOT_SUPPORTED}:
            return IndeterminateProgressBar(self)
        bar = TaskProgressBar(self)
        bar.setCustomBackgroundColor(QColor("#e3eaf6"), QColor("#34445b"))
        return bar

    def _initWidget(self) -> None:
        for widget in (self, self.nameLabel, self.statusLabel, *(getattr(self, f"{f.name}Label") for f in self.infoFields)):
            widget.setMouseTracking(True)
        self.iconLabel.setFixedSize(32, 32)
        self.checkBox.setFixedSize(23, 23)
        self.checkBox.setVisible(False)
        self.nameLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.reasonLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for spec in self.buttons:
            if spec.tooltip:
                button = getattr(self, f"{spec.name}Button")
                button.setToolTip(self.tr(spec.tooltip))
                button.installEventFilter(ToolTipFilter(button))

    def _initLayout(self) -> None:
        identity = QHBoxLayout()
        identity.setSpacing(8)
        identity.addWidget(self.checkBox)
        identity.addWidget(self.iconLabel)
        names = QVBoxLayout()
        names.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        names.setSpacing(2)
        names.addWidget(self.nameLabel)
        identity.addLayout(names, 1)

        sizeColumn = QWidget(self)
        sizeColumn.setFixedWidth(self.SIZE_WIDTH)
        sizes = QVBoxLayout(sizeColumn)
        sizes.setContentsMargins(0, 0, 0, 0)
        transfer = QVBoxLayout()
        transfer.setSpacing(4)
        transfer.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        status = QHBoxLayout()
        status.setSpacing(4)
        status.addWidget(self.statusLabel, 1)
        self.statusLabel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        transfer.addLayout(status)
        self.progressBar.setFixedHeight(4)
        transfer.addWidget(self.progressBar)
        transfer.addWidget(self.reasonLabel)
        details = QHBoxLayout()
        details.setSpacing(8)
        names.addLayout(details)
        for f in self.infoFields:
            label = getattr(self, f"{f.name}Label")
            if f.name == "size":
                sizes.addWidget(label)
            elif f.name == "speed":
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                status.addWidget(label)
            elif f.name == "eta":
                transfer.addWidget(label)
            else:
                details.addWidget(label)

        actions = QWidget(self)
        actions.setFixedWidth(self.ACTION_WIDTH)
        buttons = QHBoxLayout(actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(2)
        buttons.addStretch()
        for button in (self.checksumButton, self.openFileButton, self.toggleButton, self.moreButton):
            buttons.addWidget(button)
        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(self.COLUMN_MARGIN, 0, self.COLUMN_MARGIN, 0)
        self.hBoxLayout.setSpacing(self.COLUMN_GAP)
        self.hBoxLayout.addLayout(identity, self.NAME_STRETCH)
        self.hBoxLayout.addWidget(sizeColumn)
        self.hBoxLayout.addLayout(transfer, self.STATUS_STRETCH)
        self.hBoxLayout.addWidget(actions)
        self.openFileButton.setText(self.tr("打开文件"))

    def _bind(self) -> None:
        self.moreButton.clicked.connect(lambda: self.openMenu(self.moreButton.mapToGlobal(self.moreButton.rect().bottomLeft())))
        self.toggleButton.clicked.connect(self._onToggleClicked)
        self.checksumButton.clicked.connect(self._onChecksumClicked)
        self.openFileButton.clicked.connect(lambda: openFile(self._task.outputPath))
        self.openFolderButton.clicked.connect(lambda: revealInFolder(self._task.outputPath))
        self.deleteButton.clicked.connect(self._onDeleteClicked)
        self.checkBox.clicked.connect(lambda checked: self.selectionChanged.emit(checked, False))
        self.selectFilesButton.clicked.connect(self._onSelectFilesClicked)
        self._categoryService.categoriesChanged.connect(self._refreshIcon, owner=self)

    def refresh(self, force: bool = False) -> None:
        checksumProgress = self._taskService.checksumProgress(self._task)
        if (not force and self._lastStatus == self._task.status
                and self._task.status != TaskStatus.RUNNING and not self._task.isSeeding
                and checksumProgress is None):
            return
        self.checksumButton.setProgress(checksumProgress)

        task = self._task
        progress, speed, receivedBytes = task.currentSnapshot()
        self.progressBar.setValue(int(progress))

        for f in self.infoFields:
            label = getattr(self, f"{f.name}Label")
            formatter = f.formats.get(task.status) or f.formats.get(None)
            if (task.status == TaskStatus.FAILED and f.name != "size") or formatter is None:
                label.hide()
                continue
            text = formatter(task, speed, receivedBytes)
            if text is None:
                label.hide()
            else:
                label.setText(self.tr("剩余 {}").format(text) if f is ETA_FIELD else text)
                label.show()

        nameFormatter = self.nameFormats.get(task.status)
        if nameFormatter:
            text = nameFormatter(task, speed, receivedBytes)
            if text is not None:
                self.nameLabel.setText(text)

        if task.status == TaskStatus.RUNNING:
            text = f"{int(progress)}%" if task.fileSize > 0 else self.tr("下载中")
            self._setStatus(text)
        self._refreshForStatus(task)
        hasReason = task.status == TaskStatus.FAILED and task.lastError is not None
        self.reasonLabel.setVisible(hasReason)
        if hasReason:
            self.reasonLabel.setText(toLocalizedError(task.lastError))
        if task.status == TaskStatus.FAILED:
            self._setStatus(self.tr("下载失败"))
            self.statusLabel.setTextColor(QColor("#b33f32"), QColor("#ffaaa2"))
        elif task.status == TaskStatus.RUNNING and task.fileSize > 0 and self.statusLabel.text().endswith("%"):
            self.statusLabel.setTextColor(QColor("#1c60d7"), QColor("#8bb7ff"))
        self._refreshButtons()
        self.checksumButton.setVisible(checksumProgress is not None)
        self.openFileButton.setVisible(task.status == TaskStatus.COMPLETED)
        self.toggleButton.setVisible(task.status != TaskStatus.COMPLETED or task.canSeed)
        if task.status == TaskStatus.COMPLETED:
            self.openFileButton.setText("" if task.canSeed or checksumProgress is not None else self.tr("打开文件"))
        if self._lastStatus != task.status or task.status == TaskStatus.COMPLETED:
            label = {
                TaskStatus.RUNNING: self.tr("暂停"), TaskStatus.FAILED: self.tr("重试"),
                TaskStatus.WAITING: self.tr("开始"), TaskStatus.PAUSED: self.tr("继续"),
                TaskStatus.COMPLETED: self.tr("停止做种") if task.isSeeding else self.tr("做种"),
            }[task.status]
            if isinstance(self.toggleButton, PushButton):
                self.toggleButton.setText("" if task.status == TaskStatus.COMPLETED else label)
                self.toggleButton.setToolTip(label)
            if self._lastStatus != task.status:
                isFailed = task.status == TaskStatus.FAILED
                setCustomStyleSheet(self.toggleButton,
                                    "QAbstractButton { background: #fff1eb; color: #923a29; }" if isFailed else "",
                                    "QAbstractButton { background: #512e2a; color: #ffb5a4; }" if isFailed else "")
        self._lastStatus = task.status

    def _refreshForStatus(self, task: Task) -> None:
        self._isFileMissing = False

        if task.status == TaskStatus.RUNNING:
            self.progressBar.setError(False)
            self.progressBar.show()

        elif task.status == TaskStatus.COMPLETED:
            self.progressBar.pause()
            self.progressBar.hide()
            self._isFileMissing = task.hasOutputFile and not isExisting(task.outputPath)
            if self._isFileMissing:
                self._setStatus(self.tr("文件缺失"))
                self.statusLabel.setTextColor(QColor(200, 160, 80), QColor(200, 170, 100))
            elif task.completedAt:
                self._setStatus(self.tr("完成于 {}").format(
                    datetime.fromtimestamp(task.completedAt).strftime("%Y-%m-%d %H:%M:%S")))
            else:
                self._setStatus(self.tr("任务已经完成"))
            self.nameLabel.setText(task.name)
            self._refreshIcon()

        elif task.status == TaskStatus.FAILED:
            self.progressBar.hide()
            self.progressBar.setError(True)

        else:
            self.progressBar.setError(False)
            self.progressBar.pause()
            self.progressBar.setVisible(task.status == TaskStatus.PAUSED and task.fileSize > 0)
            if task.status == TaskStatus.PAUSED:
                self._setStatus(self.tr("任务已经暂停"))
            elif task.status == TaskStatus.WAITING:
                self._setStatus(self.tr("任务正在等待"))

    def _setStatus(self, text: str) -> None:
        if self.statusLabel.text() == text and not self.statusLabel.isHidden():
            return
        self.statusLabel.setTextColor()
        self.statusLabel.setText(text)
        self.statusLabel.show()

    def _refreshIcon(self) -> None:
        categoryId = self._categoryService.categoryOf(self._task)
        category = self._categoryService.categoryById(categoryId)
        if category is None:
            self.iconLabel.setStyleSheet("")
            self.iconLabel.setPixmap(QFileIconProvider().icon(QFileInfo(self._task.outputPath)).pixmap(32, 32))
            return
        icon = getattr(FluentIcon, category.icon, FluentIcon.DOCUMENT)
        color = {"VIDEO": "#5079dc", "ZIP_FOLDER": "#b57b24", "DOCUMENT": "#4c947a",
                 "MUSIC": "#9569b6", "PHOTO": "#438990"}.get(category.icon, "#687d99")
        self.iconLabel.setStyleSheet(f"background: {color}; border-radius: 6px;")
        self.iconLabel.setPixmap(icon.icon(Theme.DARK).pixmap(18, 18))

    def _refreshButtons(self, names=None) -> None:
        for spec in self.buttons:
            if names is not None and spec.name not in names:
                continue
            if spec.states is None:
                continue
            button = getattr(self, f"{spec.name}Button")
            fn = spec.states.get(self._task.status) or spec.states.get(None)
            state = fn(self._task) if fn else spec.default
            button.setIcon(state.icon if state.icon is not None else spec.icon)
            button.setVisible(state.visible)
            button.setEnabled(state.enabled)

    def _onToggleClicked(self) -> None:
        if self._task.isSeeding:
            self._taskService.stopSeeding(self._task)
        elif self._task.status == TaskStatus.COMPLETED:
            self._taskService.startSeeding(self._task)
        elif self._task.status == TaskStatus.RUNNING:
            self._taskService.pause(self._task)
        else:
            self._taskService.start(self._task)
        self.refresh()

    def _onSelectFilesClicked(self) -> None:
        pass

    def _onDeleteClicked(self) -> None:
        from qfluentwidgets import MessageBox
        dialog = MessageBox(self.tr("删除任务"), self.tr("确定要删除这个下载任务吗？"), self.window())
        deleteFiles = CheckBox(self.tr("同时删除已下载的文件"))
        deleteFiles.setChecked(cfg.shouldDeleteFilesOnRemove.value)
        dialog.textLayout.addWidget(deleteFiles)
        if dialog.exec():
            cfg.set(cfg.shouldDeleteFilesOnRemove, deleteFiles.isChecked())
            self._taskService.delete(self._task, deleteFiles.isChecked())
        dialog.deleteLater()

    def _onChecksumClicked(self) -> None:
        if self._taskService.checksumProgress(self._task) is not None:
            self._taskService.cancelChecksum(self._task)
            return
        if isFolder(self._task.outputPath) or not isExisting(self._task.outputPath):
            self._setStatus(self.tr("文件缺失，无法校验"))
            return
        menu = RoundMenu(parent=self)
        moreMenu = RoundMenu(self.tr("更多"), self)
        for algorithm in ALGORITHMS:
            action = Action(algorithm.upper(), self)
            action.setCheckable(True)
            action.setChecked(algorithm in self._task.checksums)
            action.triggered.connect(lambda checked=False, a=algorithm: self._taskService.startChecksum(self._task, a))
            (menu if algorithm in COMMON_ALGORITHMS else moreMenu).addAction(action)
        menu.addMenu(moreMenu)
        menu.exec(self.moreButton.mapToGlobal(self.moreButton.rect().bottomLeft()))

    def _onEditClicked(self) -> None:
        dialog = self._featureService.createEditDialog(self._task, self.window())
        dialog.exec()
        dialog.deleteLater()
        self.refresh()

    def _openDrawer(self) -> None:
        from app.view.dialogs.task_drawer import TaskDrawer
        TaskDrawer(self._task, self._taskService, self._featureService, self._categoryService, self.window()).show()

    def setSelectionMode(self, enter: bool) -> None:
        self._isSelectionMode = enter
        self.checkBox.setVisible(enter)
        if not enter:
            self.checkBox.setChecked(False)
        self.update()

    def isChecked(self) -> bool:
        return self.checkBox.isChecked()

    def setChecked(self, checked: bool) -> None:
        if checked != self.isChecked():
            self.checkBox.setChecked(checked)
            self.update()

    def createContextMenu(self) -> RoundMenu:
        menu = RoundMenu(parent=self)

        for spec in self.buttons:
            if spec.name in {"toggle", "openFile"}:
                continue
            button = getattr(self, f"{spec.name}Button")
            if spec.name == "checksum":
                if not button.isEnabled() or len(self._task.files or []) > 1 or self._task.status != TaskStatus.COMPLETED:
                    continue
            elif button.isHidden():
                continue
            action = Action(spec.icon, button.toolTip(), menu)
            action.setEnabled(button.isEnabled())
            action.triggered.connect(button.click)
            menu.addAction(action)
        menu.addSeparator()

        detail = Action(FluentIcon.INFO, self.tr("任务详情"), menu)
        detail.triggered.connect(self._openDrawer)
        menu.addAction(detail)
        menu.addSeparator()

        copyUrl = Action(FluentIcon.COPY, self.tr("复制下载链接"), menu)
        copyUrl.triggered.connect(lambda: QApplication.instance().clipboardListener.setUrls([self._task.url]))
        menu.addAction(copyUrl)

        if self._task.canEdit and self._task.status != TaskStatus.COMPLETED:
            edit = Action(FluentIcon.EDIT, self.tr("编辑任务参数..."), menu)
            edit.triggered.connect(self._onEditClicked)
            menu.addAction(edit)

        moveToFront = Action(FluentIcon.UP, self.tr("移到最前"), menu)
        moveToFront.triggered.connect(lambda: self._taskService.moveToFront([self._task.taskId]))
        menu.addAction(moveToFront)

        redownload = Action(FluentIcon.UPDATE, self.tr("重新下载"), menu)
        redownload.triggered.connect(lambda: self._taskService.redownload(self._task))
        menu.addAction(redownload)

        if cfg.isCategoryEnabled.value:
            moveMenu = RoundMenu(self.tr("移动到分类"), menu)
            moveMenu.setIcon(FluentIcon.TAG)
            uncategorized = Action(FluentIcon.MORE, self.tr("未分类"), menu)
            uncategorized.triggered.connect(lambda: self._taskService.setCategory(self._task, ""))
            moveMenu.addAction(uncategorized)
            moveMenu.addSeparator()
            for category in self._categoryService.categories():
                cid = category.categoryId
                action = Action(getattr(FluentIcon, category.icon, FluentIcon.TAG), toCategoryName(category), menu)
                action.triggered.connect(lambda checked=False, c=cid: self._taskService.setCategory(self._task, c))
                moveMenu.addAction(action)
            menu.addMenu(moveMenu)

        return menu

    def mousePressEvent(self, e) -> None:
        super().mousePressEvent(e)
        self._hasDragged = False
        if e.button() == Qt.MouseButton.LeftButton and self._canDrag():
            self._isDragPending = True
            self._dragStartPos = e.position().toPoint()

    def _matchNameText(self, pos: QPoint) -> bool:
        if self._isSelectionMode or not self.openFileButton.isEnabled():
            return False
        label = self.nameLabel
        textWidth = label.fontMetrics().horizontalAdvance(QLabel.text(label))
        return QRect(label.x() + label.indent(), label.y(), textWidth, label.height()).contains(pos)

    def _matchInfoText(self, pos: QPoint) -> bool:
        if self._isSelectionMode:
            return False
        labels = [getattr(self, f"{f.name}Label") for f in self.infoFields] + [self.statusLabel, self.reasonLabel]
        return any(label.isVisible() and label.rect().contains(label.mapFrom(self, pos)) for label in labels)

    def mouseMoveEvent(self, e) -> None:
        if not e.buttons():
            pos = e.position().toPoint()
            isLink = self._matchNameText(pos) or self._matchInfoText(pos)
            self.setCursor(Qt.CursorShape.PointingHandCursor if isLink else Qt.CursorShape.ArrowCursor)
        if self._isDragPending:
            if (e.position().toPoint() - self._dragStartPos).manhattanLength() >= QApplication.startDragDistance():
                self._isDragPending = False
                self._hasDragged = True
                self.dragRequested.emit(self._task.taskId)
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._isDragPending = False
        super().mouseReleaseEvent(e)
        if e.button() != Qt.MouseButton.LeftButton or self._hasDragged:
            return
        pos = e.position().toPoint()
        extend = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if not extend and self._matchNameText(pos):
            openFile(self._task.outputPath)
        elif not extend and self._matchInfoText(pos):
            self._openDrawer()
        else:
            checked = True if extend or not self._isSelectionMode else not self.isChecked()
            self.selectionChanged.emit(checked, extend)

    def mouseDoubleClickEvent(self, e) -> None:
        super().mouseDoubleClickEvent(e)
        if e.button() == Qt.MouseButton.LeftButton and self.openFileButton.isEnabled():
            openFile(self._task.outputPath)

    def contextMenuEvent(self, e) -> None:
        self.openMenu(e.globalPos())
        e.accept()

    def openMenu(self, pos: QPoint) -> None:
        menu = self.createContextMenu()
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.exec(pos)

    def paintEvent(self, e) -> None:
        with QPainter(self) as painter:
            if self._isSelectionMode and self.isChecked():
                color = themeColor()
                color.setAlpha(40)
                painter.fillRect(self.rect(), color)
            painter.setPen(QColor(128, 128, 128, 60))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

    def _canDrag(self) -> bool:
        return (self._task.status == TaskStatus.COMPLETED
                and self._task.hasOutputFile
                and isExisting(self._task.outputPath))


class MultiFileTaskCard(TaskCard):
    fileSelectDialog: type[MessageBoxBase] | None = None

    def _onSelectFilesClicked(self) -> None:
        if self.fileSelectDialog is None:
            return
        dialog = self.fileSelectDialog(self._task, self._categoryService, self.window())
        try:
            if dialog.exec():
                self._taskService.updateSelection(self._task, dialog.selectedIndexes())
                self.refresh(force=True)
        finally:
            dialog.deleteLater()

    def _refreshForStatus(self, task: Task) -> None:
        super()._refreshForStatus(task)
        if (task.files and len(task.files) > 1
                and not self._isFileMissing
                and task.status in {TaskStatus.WAITING, TaskStatus.COMPLETED}):
            selected = sum(1 for f in task.files if f.selected)
            self.statusLabel.setText(
                self.tr("{0}/{1} 个文件").format(selected, len(task.files))
            )
