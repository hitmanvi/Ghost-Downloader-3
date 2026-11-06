from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QSize, Signal, QThread, QTimer, QUrl
from PySide6.QtGui import QActionGroup, QColor, QCursor, QDesktopServices, QKeySequence, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QPushButton, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action, CaptionLabel, CheckableMenu, CommandBarView, DropDownToolButton,
    FluentIcon, Flyout, FlyoutView, IconWidget, InfoBar, InfoBarPosition, MenuIndicatorType, PushButton,
    RoundMenu, PlainTextEdit, SpinBox, SubtitleLabel, PrimaryPushButton, DropDownPushButton, SwitchButton,
    isDarkTheme, qconfig, setCustomStyleSheet, TransparentPushButton,
)

from app.view.components.control_sizes import CONTROL_HEIGHT, CONTROL_ICON_SIZE
from app.view.components.sidebar import Sidebar
from app.view.components.band_selector import BandSelector
from app.view.components.scroll_area import FastScrollArea, PAGE_MARGIN

from app.config.cfg import cfg
from app.format import toReadableSize
from app.i18n import toLocalizedError
from app.models.task import TaskError, TaskStatus
from app.platform.desktop import openFolder
from app.platform.filesystem import isExisting
from app.view.cards.task_cards import TaskCard
from app.view.components.category_settings import toCategoryName
from app.view.components.labels import IconBodyLabel

if TYPE_CHECKING:
    from app.models.task import Task


class FilterMode(IntEnum):
    ALL = 0
    ACTIVE = 1
    PAUSED = 2
    COMPLETED = 3
    FAILED = 4


FILTER_TO_STATUSES = {
    FilterMode.ACTIVE: {TaskStatus.RUNNING, TaskStatus.WAITING},
    FilterMode.PAUSED: {TaskStatus.PAUSED},
    FilterMode.COMPLETED: {TaskStatus.COMPLETED},
    FilterMode.FAILED: {TaskStatus.FAILED},
}


class SortField(IntEnum):
    CREATED_AT = 0
    NAME = 1
    SIZE = 2
    COMPLETED_AT = 3
    QUEUE_POSITION = 4


class TaskCommandBarView(CommandBarView):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.moveToFrontAction = Action(FluentIcon.UP, self.tr("移到最前"), self)
        self.redownloadAction = Action(FluentIcon.UPDATE, self.tr("重新下载"), self)
        self.deleteAction = Action(FluentIcon.DELETE, self.tr("删除"), self)
        self.copyUrlAction = Action(FluentIcon.COPY, self.tr("复制链接"), self)
        self.moveCategoryAction = Action(FluentIcon.TAG, self.tr("移动到分类"), self)
        self.selectAllAction = Action(FluentIcon.CLEAR_SELECTION, self.tr("全选"), self)
        self.selectMissingAction = Action(FluentIcon.REMOVE, self.tr("选择缺失"), self)
        self.invertSelectAction = Action(FluentIcon.CUT, self.tr("反选"), self)
        self.cancelAction = Action(FluentIcon.CLEAR_SELECTION, self.tr("取消全选"), self)

        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(18, 18))
        self.addAction(self.moveToFrontAction)
        self.addAction(self.redownloadAction)
        self.addAction(self.deleteAction)
        self.addAction(self.copyUrlAction)
        self.addAction(self.moveCategoryAction)
        self.addSeparator()
        self.addAction(self.selectAllAction)
        self.addAction(self.selectMissingAction)
        self.addAction(self.invertSelectAction)
        self.addAction(self.cancelAction)
        self.resizeToSuitableWidth()

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(35)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 80 if isDarkTheme() else 30))
        self.setGraphicsEffect(shadow)

        self.moveCategoryAction.setVisible(cfg.isCategoryEnabled.value)
        cfg.isCategoryEnabled.valueChanged.connect(self._onCategoryEnabledChanged)

    def _onCategoryEnabledChanged(self, value) -> None:
        self.moveCategoryAction.setVisible(bool(value))


class EmptyStatusWidget(QWidget):

    def __init__(self, icon, text, parent=None):
        super().__init__(parent)
        self.iconWidget = IconWidget(icon)
        self.iconWidget.setFixedSize(64, 64)
        self.label = CaptionLabel(text)
        self.label.setTextColor(QColor(96, 96, 96), QColor(216, 216, 216))
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.borderRadius = 10

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.addWidget(self.iconWidget, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignHCenter)

    def setText(self, text: str) -> None:
        self.label.setText(text)

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(255, 255, 255, 13 if isDarkTheme() else 200))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), self.borderRadius, self.borderRadius)


class TaskPage(QWidget):
    urlsRequested = Signal(list)
    ROW_SPACING = 0
    VIEWPORT_BUFFER = 5

    def __init__(self, taskService, featureService, categoryService, speedMeter, plan=None, parent=None):
        super().__init__(parent)
        self._taskService = taskService
        self._featureService = featureService
        self._categoryService = categoryService
        self._speedMeter = speedMeter
        self._plan = plan
        self._filterMode = FilterMode.ALL
        self._sortField = SortField.CREATED_AT
        self._sortAscending = False
        self._searchText = ""
        self._isSelectionMode = False
        self._selectionAnchor: str | None = None
        self._liveCards: dict[str, TaskCard] = {}
        self._pendingUnmounts: list[TaskCard] = []
        self._displayOrder: list[str] = []
        self._selectedIds: set[str] = set()
        self._bandSnapshot: set[str] = set()
        self._bandMerge = set.__or__
        self._tickingIds: set[str] = set()

        self._refreshListTimer = QTimer(self, singleShot=True)
        self._refreshListTimer.setInterval(0)
        self._refreshListTimer.timeout.connect(self._refreshList)

        self._cardRefreshTimer = QTimer(self)
        self._cardRefreshTimer.setInterval(1000)
        self._cardRefreshTimer.timeout.connect(self._refreshTickingCards)

        self.scrollArea = FastScrollArea(self)
        self.scrollWidget = QWidget(self)
        self.emptyStatusWidget = EmptyStatusWidget(FluentIcon.EMOJI_TAB_SYMBOLS, self.tr("暂无下载任务"), self)

        self.sidebar = Sidebar("", self)
        self.downloadButton = self.sidebar.add(self.tr("下载任务"), FluentIcon.DOWNLOAD)
        self.downloadButton.setCheckable(True)
        shortcuts = self.sidebar.addSection(self.tr("快捷操作"))
        self.folderButton = self.sidebar.add(self.tr("打开下载文件夹"), FluentIcon.FOLDER, section=shortcuts)
        self.planButton = self.sidebar.add(self.tr("计划任务"), FluentIcon.DATE_TIME, section=shortcuts)
        self.planButton.setCheckable(True)
        self.planButton.setVisible(plan is not None)
        self.filterNames = {
            FilterMode.ALL: self.tr("全部"), FilterMode.ACTIVE: self.tr("进行中"),
            FilterMode.PAUSED: self.tr("已暂停"), FilterMode.COMPLETED: self.tr("已完成"),
            FilterMode.FAILED: self.tr("失败"),
        }
        self.tabs = QWidget(self)
        self.tabs.setObjectName("taskTabs")
        self.tabs.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.filterButtons = {}
        for mode, name in self.filterNames.items():
            button = QPushButton(name, self.tabs)
            button.setCheckable(True)
            self.filterButtons[mode] = button
        self.toolBar = QWidget(self)
        self.heading = SubtitleLabel(self.tr("下载任务"), self)
        self.newButton = PrimaryPushButton(FluentIcon.ADD, self.tr("新建任务"), self)
        self.pauseAllButton = TransparentPushButton(FluentIcon.PAUSE, self.tr("全部暂停"), self.toolBar)
        self.columns = QWidget(self)
        self.columns.setObjectName("taskColumns")
        self.columns.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.moreButton = DropDownToolButton(FluentIcon.MORE, self.toolBar)
        self.moreMenu = RoundMenu(parent=self.moreButton)
        self.startAllAction = Action(FluentIcon.PLAY, self.tr("全部开始"), self)
        self.selectAction = Action(FluentIcon.CLEAR_SELECTION, self.tr("选择任务"), self)
        self.rateLimitButton = PushButton(FluentIcon.SPEED_OFF, self.tr("不限速"), self)
        self.speedBadge = IconBodyLabel("0 B/s", FluentIcon.SPEED_HIGH, self)
        self.sortButton = DropDownPushButton(FluentIcon.LAYOUT, self.tr("按添加时间"), self.toolBar)

        self.firstTask = QWidget(self)
        self.urlEdit = PlainTextEdit(self.firstTask)
        self.urlEdit.setPlaceholderText(self.tr("输入下载链接，每行一个"))
        self.urlEdit.setFixedHeight(96)
        self.urlEdit.setAcceptDrops(False)
        self.pasteButton = PushButton(FluentIcon.PASTE, self.tr("粘贴链接"), self.firstTask)
        self.addButton = PrimaryPushButton(FluentIcon.ADD, self.tr("新建任务"), self.firstTask)

        # sort menu
        self.sortMenu = CheckableMenu(parent=self, indicatorType=MenuIndicatorType.RADIO)
        self.sortFieldGroup = QActionGroup(self)
        self.sortOrderGroup = QActionGroup(self)
        self.createdAtSortAction = Action(FluentIcon.DATE_TIME, self.tr("按添加时间"), self, checkable=True)
        self.completedAtSortAction = Action(FluentIcon.HISTORY, self.tr("按完成时间"), self, checkable=True)
        self.nameSortAction = Action(FluentIcon.FONT, self.tr("按名称排序"), self, checkable=True)
        self.sizeSortAction = Action(FluentIcon.LIBRARY, self.tr("按大小排序"), self, checkable=True)
        self.queuePositionSortAction = Action(FluentIcon.SCROLL, self.tr("按队列顺序"), self, checkable=True)
        self.ascendingAction = Action(FluentIcon.UP, self.tr("顺序"), self, checkable=True)
        self.descendingAction = Action(FluentIcon.DOWN, self.tr("倒序"), self, checkable=True)

        # selection command bar
        self.commandView = TaskCommandBarView(self)
        self.commandView.hide()

        self._initWidget()
        self._initLayout()
        self._bind()

    def _initWidget(self) -> None:
        self.setObjectName("TaskPage")
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.enableTransparentBackground()

        self.sortActions = {
            SortField.CREATED_AT: self.createdAtSortAction,
            SortField.COMPLETED_AT: self.completedAtSortAction,
            SortField.NAME: self.nameSortAction,
            SortField.SIZE: self.sizeSortAction,
            SortField.QUEUE_POSITION: self.queuePositionSortAction,
        }
        for action in self.sortActions.values():
            self.sortFieldGroup.addAction(action)
        self.sortOrderGroup.addAction(self.ascendingAction)
        self.sortOrderGroup.addAction(self.descendingAction)
        self.sortMenu.addAction(self.createdAtSortAction)
        self.sortMenu.addAction(self.completedAtSortAction)
        self.sortMenu.addAction(self.nameSortAction)
        self.sortMenu.addAction(self.sizeSortAction)
        self.sortMenu.addAction(self.queuePositionSortAction)
        self.sortMenu.addSeparator()
        self.sortMenu.addAction(self.ascendingAction)
        self.sortMenu.addAction(self.descendingAction)
        self.sortButton.setMenu(self.sortMenu)
        self.createdAtSortAction.setChecked(True)
        self.descendingAction.setChecked(True)

        self.moreMenu.addActions([self.startAllAction, self.selectAction])
        self.moreButton.setMenu(self.moreMenu)
        self.moreButton.setAccessibleName(self.tr("任务操作"))
        self._onSpeedLimitChanged()
        for button in (self.newButton, self.addButton, self.pasteButton, self.pauseAllButton,
                       self.sortButton, self.moreButton, self.rateLimitButton):
            button.setFixedHeight(CONTROL_HEIGHT)
            button.setIconSize(QSize(CONTROL_ICON_SIZE, CONTROL_ICON_SIZE))
            button.setObjectName("taskControl")
            light = dark = "#taskControl { padding-top: 1px; padding-bottom: 1px; font-size: 13px; }"
            if button in (self.newButton, self.addButton):
                light += "PrimaryPushButton { background-color: #185ed9; color: white; }"
                dark += "PrimaryPushButton { background-color: #2465d9; color: white; }"
            setCustomStyleSheet(button, light, dark)
        setCustomStyleSheet(self.urlEdit, "PlainTextEdit { background: #f5f8fd; }",
                            "PlainTextEdit { background: #1e2a3b; }")
        self.speedBadge.setTextColor(QColor("#1c60d7"), QColor("#8bb7ff"))
        self._refreshTheme()

        self.emptyStatusWidget.setMinimumWidth(200)
        self.emptyStatusWidget.adjustSize()

    def _initLayout(self) -> None:
        header = QHBoxLayout()
        header.setContentsMargins(28, 8, 28, 8)
        header.setSpacing(8)
        header.addWidget(self.heading)
        header.addStretch()
        self.searchLayout = QHBoxLayout()
        header.addLayout(self.searchLayout)
        header.addWidget(self.newButton)

        tabs = QHBoxLayout(self.tabs)
        tabs.setContentsMargins(28, 0, 28, 0)
        tabs.setSpacing(16)
        for button in self.filterButtons.values():
            tabs.addWidget(button)
        tabs.addStretch()

        tools = QHBoxLayout(self.toolBar)
        tools.setContentsMargins(28, 0, 28, 0)
        tools.addWidget(self.pauseAllButton)
        tools.addStretch()
        tools.addWidget(self.sortButton)
        tools.addWidget(self.moreButton)

        columns = QHBoxLayout(self.columns)
        columns.setContentsMargins(PAGE_MARGIN + TaskCard.COLUMN_MARGIN, 0,
                                   PAGE_MARGIN + TaskCard.COLUMN_MARGIN, 0)
        columns.setSpacing(TaskCard.COLUMN_GAP)
        columns.addWidget(CaptionLabel(self.tr("名称"), self.columns), TaskCard.NAME_STRETCH)
        size = CaptionLabel(self.tr("大小"), self.columns)
        size.setFixedWidth(TaskCard.SIZE_WIDTH)
        columns.addWidget(size)
        columns.addWidget(CaptionLabel(self.tr("状态"), self.columns), TaskCard.STATUS_STRETCH)
        actions = QWidget(self.columns)
        actions.setFixedWidth(TaskCard.ACTION_WIDTH)
        columns.addWidget(actions)
        self.columns.setFixedHeight(28)

        form = QWidget(self.firstTask)
        fields = QVBoxLayout(form)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(12)
        fields.addWidget(SubtitleLabel(self.tr("添加第一个任务"), form))
        fields.addWidget(self.urlEdit)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.pasteButton)
        actions.addStretch()
        actions.addWidget(self.addButton)
        fields.addLayout(actions)
        fields.addWidget(CaptionLabel(self.tr("也可以将种子文件拖入窗口"), form))
        empty = QVBoxLayout(self.firstTask)
        empty.setContentsMargins(28, 16, 28, 16)
        empty.addStretch()
        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(form, 8)
        center.addStretch(1)
        form.setMaximumWidth(528)
        empty.addLayout(center)
        empty.addStretch()

        footerWidget = QWidget(self)
        footerWidget.setObjectName("taskFooter")
        footerWidget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        footerWidget.setStyleSheet("#taskFooter { border-top: 1px solid rgba(105, 150, 160, 80); background: transparent; }")
        footer = QHBoxLayout(footerWidget)
        footer.setContentsMargins(28, 4, 28, 8)
        footer.addWidget(CaptionLabel(self.tr("总下载速度"), footerWidget))
        footer.addWidget(self.speedBadge)
        footer.addStretch()
        footer.addWidget(self.rateLimitButton)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self.tabs)
        layout.addWidget(self.toolBar)
        layout.addWidget(self.columns)
        layout.addWidget(self.firstTask, 1)
        layout.addWidget(self.scrollArea, 1)
        layout.addWidget(footerWidget)

    def _bind(self) -> None:
        self._taskService.taskAdded.connect(self._onTaskAdded, owner=self)
        self._taskService.taskRemoved.connect(self._onTaskRemoved, owner=self)
        self._taskService.taskStarted.connect(self._onTaskStarted, owner=self)
        self._taskService.taskPaused.connect(self._onTaskStopped, owner=self)
        self._taskService.taskCompleted.connect(self._onTaskStopped, owner=self)
        self._taskService.taskFailed.connect(self._onTaskStopped, owner=self)
        self._taskService.seedingStarted.connect(self._onTickingStarted, owner=self)
        self._taskService.seedingStopped.connect(self._onTickingStopped, owner=self)
        self._taskService.checksumStarted.connect(self._onTickingStarted, owner=self)
        self._taskService.checksumCompleted.connect(self._onChecksumCompleted, owner=self)
        self._taskService.checksumStopped.connect(self._onChecksumStopped, owner=self)
        self._taskService.queueChanged.connect(self._refreshListTimer.start, owner=self)
        self._taskService.fileDisappeared.connect(self._onFileDisappeared, owner=self)
        self._taskService.fileDeleteDenied.connect(self._onFileDeleteDenied, owner=self)
        self._taskService.overwriteFailed.connect(self._onOverwriteFailed, owner=self)
        self._taskService.editFailed.connect(self._onEditFailed, owner=self)
        self._speedMeter.speedChanged.connect(self._onSpeedChanged, owner=self)
        self.scrollArea.verticalScrollBar().valueChanged.connect(self._refreshViewport)

        self.newButton.clicked.connect(lambda: self.urlsRequested.emit([]))
        self.pasteButton.clicked.connect(self._onPasteClicked)
        self.addButton.clicked.connect(self._onUrlsRequested)
        self.startAllAction.triggered.connect(self.startAll)
        self.pauseAllButton.clicked.connect(self.pauseAll)
        self.selectAction.triggered.connect(lambda: self.setSelectionMode(not self._isSelectionMode))
        self.folderButton.clicked.connect(lambda: openFolder(cfg.downloadFolder.value))
        self.planButton.clicked.connect(self._onPlanButtonClicked)
        self.rateLimitButton.clicked.connect(self._onRateLimitMenuRequested)
        cfg.isSpeedLimitEnabled.valueChanged.connect(self._onSpeedLimitChanged)
        cfg.speedLimitation.valueChanged.connect(self._onSpeedLimitChanged)

        for field, action in self.sortActions.items():
            action.triggered.connect(lambda _, field=field: self.setSortField(field))
        self.ascendingAction.triggered.connect(lambda: self.setSortOrder(True))
        self.descendingAction.triggered.connect(lambda: self.setSortOrder(False))

        for mode, button in self.filterButtons.items():
            button.clicked.connect(lambda _, mode=mode: self.setFilterMode(mode))

        self.commandView.moveToFrontAction.triggered.connect(self._onMoveToFrontSelected)
        self.commandView.redownloadAction.triggered.connect(self._onRedownloadSelected)
        self.commandView.deleteAction.triggered.connect(self._onDeleteSelected)
        self.commandView.copyUrlAction.triggered.connect(self._onCopyUrlSelected)
        self.commandView.moveCategoryAction.triggered.connect(self._onMoveCategorySelected)
        self.commandView.selectAllAction.triggered.connect(self.selectAll)
        self.commandView.selectMissingAction.triggered.connect(self.selectMissing)
        self.commandView.invertSelectAction.triggered.connect(self.invertSelection)
        self.commandView.cancelAction.triggered.connect(lambda: self.setSelectionMode(False))

        qconfig.themeChangedFinished.connect(self._refreshTheme)

        self._bandSelector = BandSelector(
            self.scrollArea, self.scrollWidget,
            TaskCard.ROW_HEIGHT, self.ROW_SPACING, self
        )
        self._bandSelector.dragStarted.connect(self._onBandDragStarted)
        self._bandSelector.bandChanged.connect(self._onBandChanged)
        self._bandSelector.dragFinished.connect(self._onBandDragFinished)

        self._refreshListTimer.start()

    # ── intent methods ──

    def _onPasteClicked(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text:
            self.urlEdit.appendPlainText(text)
        self.urlEdit.setFocus()

    def _onUrlsRequested(self) -> None:
        urls = [line.strip() for line in self.urlEdit.toPlainText().splitlines() if line.strip()]
        self.urlEdit.clear()
        self.urlsRequested.emit(urls)

    def setFilterMode(self, mode: FilterMode) -> None:
        if mode != self._filterMode:
            self.setSelectionMode(False)
        self._filterMode = mode
        self._refreshList()

    def setSortField(self, field: SortField) -> None:
        self._sortField = field
        action = self.sortActions[field]
        action.setChecked(True)
        self.sortButton.setText(action.text())
        self._refreshList()

    def setSortOrder(self, ascending: bool) -> None:
        self._sortAscending = ascending
        self._refreshList()

    @property
    def searchPlaceholder(self) -> str:
        return self.tr("搜索任务")

    def setSearchText(self, text: str) -> None:
        self._searchText = text
        self._refreshList()

    def startAll(self) -> None:
        self._taskService.startAll()

    def pauseAll(self) -> None:
        self._taskService.pauseAll()

    def selectAll(self) -> None:
        self._selectedIds.update(self._displayOrder)
        for card in self._liveCards.values():
            card.setChecked(True)

    def invertSelection(self) -> None:
        self._selectedIds ^= set(self._displayOrder)
        for taskId, card in self._liveCards.items():
            card.setChecked(taskId in self._selectedIds)

    def selectMissing(self) -> None:
        self._selectedIds.clear()
        for taskId in self._displayOrder:
            task = self._taskService.taskById(taskId)
            if task and task.hasOutputFile and not isExisting(task.outputPath):
                self._selectedIds.add(taskId)
        for taskId, card in self._liveCards.items():
            card.setChecked(taskId in self._selectedIds)

    def setSelectionMode(self, enter: bool) -> None:
        if self._isSelectionMode == enter:
            return
        self._isSelectionMode = enter
        self._selectionAnchor = None
        if not enter:
            self._selectedIds.clear()
        for card in self._liveCards.values():
            card.setSelectionMode(enter)
            if not enter:
                card.setChecked(False)
        self.commandView.setVisible(enter)
        if enter:
            self.commandView.raise_()

    def _onCardSelectionChanged(self, taskId: str, checked: bool, extend: bool) -> None:
        if not self._isSelectionMode:
            self.setSelectionMode(True)

        if extend and self._selectionAnchor:
            try:
                anchorIdx = self._displayOrder.index(self._selectionAnchor)
                currentIdx = self._displayOrder.index(taskId)
            except ValueError:
                return
            self._selectedIds.clear()
            for i in range(min(anchorIdx, currentIdx), max(anchorIdx, currentIdx) + 1):
                self._selectedIds.add(self._displayOrder[i])
            for tid, card in self._liveCards.items():
                card.setChecked(tid in self._selectedIds)
        else:
            if checked:
                self._selectedIds.add(taskId)
            else:
                self._selectedIds.discard(taskId)
            card = self._liveCards.get(taskId)
            if card:
                card.setChecked(checked)
            if checked:
                self._selectionAnchor = taskId
            elif taskId == self._selectionAnchor:
                self._selectionAnchor = None

        if not self._selectedIds:
            self.setSelectionMode(False)

    @property
    def isSelectionMode(self) -> bool:
        return self._isSelectionMode

    @property
    def isSortAscending(self) -> bool:
        return self._sortAscending

    # ── toolbar handlers ──

    def _onSpeedChanged(self, speed: int) -> None:
        self.speedBadge.setText(f"{toReadableSize(speed)}/s")

    def _onSpeedLimitChanged(self, _value=None) -> None:
        limit = f"{toReadableSize(cfg.speedLimitation.value)}/s"
        self.rateLimitButton.setText(limit if cfg.isSpeedLimitEnabled.value else self.tr("不限速"))

    def _onRateLimitMenuRequested(self) -> None:
        spinBox = SpinBox()
        spinBox.setSingleStep(512)
        spinBox.setMinimumWidth(180)
        spinBox.setSuffix(" KB/s")
        spinBox.setKeyboardTracking(False)
        low, high = cfg.speedLimitation.range
        spinBox.setRange(low // 1024, high // 1024)
        spinBox.setValue(cfg.speedLimitation.value // 1024)
        spinBox.valueChanged.connect(self._onRateLimitValueChanged)
        view = FlyoutView(self.tr("下载限速"), "")
        switch = SwitchButton(self.tr("启用限速"), view)
        switch.setChecked(cfg.isSpeedLimitEnabled.value)
        switch.checkedChanged.connect(lambda enabled: cfg.set(cfg.isSpeedLimitEnabled, enabled))
        spinBox.valueChanged.connect(lambda _: switch.setChecked(True))
        view.addWidget(switch)
        view.addWidget(spinBox)
        Flyout.make(view, self.rateLimitButton, self.window())

    def _onRateLimitValueChanged(self, value: int) -> None:
        cfg.set(cfg.speedLimitation, value * 1024)
        cfg.set(cfg.isSpeedLimitEnabled, True)

    def _onPlanButtonClicked(self) -> None:
        plan = self._plan
        if plan is None:
            return
        if self.planButton.isChecked():
            from app.view.dialogs.plan_task import PlanTaskDialog
            dialog = PlanTaskDialog(self.window())
            if dialog.exec():
                plan.set(dialog.selectedAction(), dialog.selectedFilePath(),
                         onCleared=lambda: self.planButton.setChecked(False))
            else:
                self.planButton.setChecked(False)
            dialog.deleteLater()
        else:
            plan.clear()

    def _refreshTheme(self) -> None:
        if isDarkTheme():
            ink, accent, line, tint = "#aab6ca", "#8bb7ff", "#344052", "#1e2a3b"
        else:
            ink, accent, line, tint = "#5b677c", "#1c60d7", "#dde3ed", "#f5f8fd"
        self.tabs.setStyleSheet(f"""
            #taskTabs {{ border-bottom: 1px solid {line}; }}
            #taskTabs QPushButton {{ color: {ink}; background: transparent; border: none;
                padding: 6px 2px 8px; border-bottom: 3px solid transparent; }}
            #taskTabs QPushButton:checked {{ color: {accent}; border-bottom-color: {accent}; }}
            #taskTabs QPushButton:hover, #taskTabs QPushButton:focus {{ background: {tint}; }}
        """)
        retryInk, retryFill = ("#ffb5a4", "#512e2a") if isDarkTheme() else ("#923a29", "#fff1eb")
        self.scrollWidget.setStyleSheet(f"""
            #rowAction {{ font-size: 12px; padding: 0 5px; color: {ink}; background: transparent;
                border: 1px solid {line}; border-radius: 4px; }}
            #rowAction[failed="true"] {{ color: {retryInk}; background: {retryFill}; }}
            #rowAction:hover, #rowAction:focus {{ border-color: {accent}; }}
            #rowAction:pressed {{ background: {tint}; }}
            #rowAction:disabled {{ color: palette(mid); border-color: {line}; }}
        """)
        self.columns.setStyleSheet(f"#taskColumns {{ background: {tint}; }}")

    def _onMoveToFrontSelected(self) -> None:
        self._taskService.moveToFront(
            [tid for tid in self._displayOrder if tid in self._selectedIds]
        )
        self.setSelectionMode(False)

    def _onCopyUrlSelected(self) -> None:
        urls = [
            task.url for taskId in self._displayOrder
            if taskId in self._selectedIds and (task := self._taskService.taskById(taskId))
        ]
        QApplication.instance().clipboardListener.setUrls(urls)

    def _onRedownloadSelected(self) -> None:
        for taskId in self._displayOrder:
            if taskId in self._selectedIds:
                task = self._taskService.taskById(taskId)
                if task:
                    self._taskService.redownload(task)
        self.setSelectionMode(False)

    def _onDeleteSelected(self) -> None:
        from qfluentwidgets import CheckBox, MessageBox
        dialog = MessageBox(self.tr("删除任务"), self.tr("确定要删除选中的下载任务吗？"), self.window())
        deleteFiles = CheckBox(self.tr("同时删除已下载的文件"))
        deleteFiles.setChecked(cfg.shouldDeleteFilesOnRemove.value)
        dialog.textLayout.addWidget(deleteFiles)
        if dialog.exec():
            cfg.set(cfg.shouldDeleteFilesOnRemove, deleteFiles.isChecked())
            self._onDeleteConfirmed(deleteFiles.isChecked())
        dialog.deleteLater()

    def _onMoveCategorySelected(self) -> None:
        targets = [
            task for taskId in self._displayOrder
            if taskId in self._selectedIds and (task := self._taskService.taskById(taskId))
        ]
        if not targets:
            return

        def moveTo(categoryId):
            for task in targets:
                self._taskService.setCategory(task, categoryId)
            self.setSelectionMode(False)

        popup = RoundMenu(parent=self)
        uncategorized = Action(FluentIcon.MORE, self.tr("未分类"), self)
        uncategorized.triggered.connect(lambda: moveTo(""))
        popup.addAction(uncategorized)
        popup.addSeparator()
        for category in self._categoryService.categories():
            cid = category.categoryId
            action = Action(getattr(FluentIcon, category.icon, FluentIcon.TAG), toCategoryName(category), self)
            action.triggered.connect(lambda checked=False, c=cid: moveTo(c))
            popup.addAction(action)
        popup.exec(QCursor.pos())

    def _onDeleteConfirmed(self, shouldDeleteFiles: bool) -> None:
        for taskId in list(self._selectedIds & set(self._displayOrder)):
            task = self._taskService.taskById(taskId)
            if task:
                self._taskService.delete(task, shouldDeleteFiles)
        self.setSelectionMode(False)

    # ── list management ──

    def _refreshList(self) -> None:
        tasks = self._taskService.tasks
        isFirstTask = not tasks and self._filterMode == FilterMode.ALL and not self._searchText
        self.firstTask.setVisible(isFirstTask)
        self.newButton.setVisible(not isFirstTask)
        self.toolBar.setVisible(not isFirstTask)
        self.columns.setVisible(not isFirstTask)
        self.scrollArea.setVisible(not isFirstTask)

        for mode, button in self.filterButtons.items():
            count = len(tasks) if mode == FilterMode.ALL else sum(t.status in FILTER_TO_STATUSES[mode] for t in tasks)
            name = self.filterNames[mode]
            button.setText(f"{name}  {count}" if count else name)
            button.setChecked(mode == self._filterMode)

        if self._filterMode != FilterMode.ALL:
            statuses = FILTER_TO_STATUSES.get(self._filterMode)
            if statuses is not None:
                tasks = [t for t in tasks if t.status in statuses]

        if self._searchText:
            lower = self._searchText.lower()
            tasks = [t for t in tasks if lower in t.name.lower() or lower in t.url.lower()]

        if self._sortField == SortField.QUEUE_POSITION:
            waitingIndex = {tid: i for i, tid in enumerate(self._taskService.waitingOrder())}
            running, waiting, rest = [], [], []
            for t in tasks:
                if t.status == TaskStatus.RUNNING:
                    running.append(t)
                elif t.taskId in waitingIndex:
                    waiting.append(t)
                else:
                    rest.append(t)
            waiting.sort(key=lambda t: waitingIndex[t.taskId])
            rest.sort(key=lambda t: t.createdAt, reverse=True)
            tasks = running + waiting + rest if not self._sortAscending else rest + waiting + running
        elif self._sortField == SortField.NAME:
            tasks.sort(key=lambda t: t.name.lower(), reverse=not self._sortAscending)
        elif self._sortField == SortField.SIZE:
            tasks.sort(key=lambda t: t.fileSize, reverse=not self._sortAscending)
        elif self._sortField == SortField.COMPLETED_AT:
            desc = not self._sortAscending
            completed = [t for t in tasks if t.completedAt]
            pending = [t for t in tasks if not t.completedAt]
            completed.sort(key=lambda t: t.completedAt, reverse=desc)
            pending.sort(key=lambda t: t.createdAt, reverse=desc)
            tasks = pending + completed if desc else completed + pending
        else:
            tasks.sort(key=lambda t: t.createdAt, reverse=not self._sortAscending)

        self._displayOrder = [t.taskId for t in tasks]
        self._tickingIds = {t.taskId for t in self._taskService.tasks
                            if t.status == TaskStatus.RUNNING or t.isSeeding}
        if self._tickingIds and not self._cardRefreshTimer.isActive():
            self._cardRefreshTimer.start()
        elif not self._tickingIds and self._cardRefreshTimer.isActive():
            self._cardRefreshTimer.stop()
        stride = TaskCard.ROW_HEIGHT + self.ROW_SPACING
        count = len(self._displayOrder)
        self.scrollWidget.setMinimumHeight(
            count * stride - self.ROW_SPACING + PAGE_MARGIN if count else 0
        )
        self._bandSelector.setItemCount(count)
        self._refreshViewport()

        if self._displayOrder or isFirstTask:
            self.emptyStatusWidget.hide()
        else:
            if not self._taskService.tasks:
                text = self.tr("暂无下载任务")
            elif self._searchText and self._filterMode != FilterMode.ALL:
                text = self.tr("没有匹配筛选条件的任务")
            elif self._searchText:
                text = self.tr("没有匹配的任务")
            elif self._filterMode == FilterMode.ACTIVE:
                text = self.tr("暂无活动任务")
            elif self._filterMode == FilterMode.COMPLETED:
                text = self.tr("暂无完成任务")
            elif self._filterMode == FilterMode.PAUSED:
                text = self.tr("暂无暂停任务")
            elif self._filterMode == FilterMode.FAILED:
                text = self.tr("暂无失败任务")
            else:
                text = self.tr("暂无下载任务")
            self.emptyStatusWidget.setText(text)
            self.emptyStatusWidget.adjustSize()
            self.emptyStatusWidget.show()

    def _unmountCard(self, card: TaskCard) -> None:
        card.hide()
        # 嵌套事件循环（对话框 exec 等）可能正挂在卡片的栈帧上，
        # 此刻销毁会让栈回退进已删控件；推迟到回到主循环后的下次刷新
        if QThread.currentThread().loopLevel() > 1:
            self._pendingUnmounts.append(card)
        else:
            card.deleteLater()

    def _refreshViewport(self) -> None:
        if self._pendingUnmounts and QThread.currentThread().loopLevel() == 1:
            for card in self._pendingUnmounts:
                card.deleteLater()
            self._pendingUnmounts.clear()

        stride = TaskCard.ROW_HEIGHT + self.ROW_SPACING
        width = self.scrollWidget.width()
        # 视口给滚动条让出的位置也算右侧留白，和工具栏对齐
        rightPadding = max(0, PAGE_MARGIN - self.scrollArea.viewportMargins().right())
        count = len(self._displayOrder)
        if count:
            top = self.scrollArea.verticalScrollBar().value()
            height = self.scrollArea.viewport().height()
            first = max(0, top // stride - self.VIEWPORT_BUFFER)
            last = min(count - 1, (top + height) // stride + self.VIEWPORT_BUFFER)
        else:
            first, last = 0, -1

        desired: set[str] = {self._displayOrder[i] for i in range(first, last + 1)}

        for taskId in list(self._liveCards.keys() - desired):
            self._unmountCard(self._liveCards.pop(taskId))

        for idx in range(first, last + 1):
            taskId = self._displayOrder[idx]
            card = self._liveCards.get(taskId)
            if card is None:
                task = self._taskService.taskById(taskId)
                if task is None:
                    continue
                card = self._createCard(task)
                if card is None:
                    continue
                card.setSelectionMode(self._isSelectionMode)
                if taskId in self._selectedIds:
                    card.setChecked(True)
                card.selectionChanged.connect(
                    lambda checked, extend, tid=taskId: self._onCardSelectionChanged(tid, checked, extend)
                )
                card.dragRequested.connect(self._onCardDragRequested)
                self._liveCards[taskId] = card
                card.refresh()
            card.setGeometry(PAGE_MARGIN, idx * stride, max(0, width - PAGE_MARGIN - rightPadding), TaskCard.ROW_HEIGHT)
            card.show()

    def _refreshTickingCards(self) -> None:
        for taskId in self._tickingIds:
            card = self._liveCards.get(taskId)
            if card is not None:
                card.refresh()

    def _onTaskStarted(self, task: Task) -> None:
        self._tickingIds.add(task.taskId)
        card = self._liveCards.get(task.taskId)
        if card is not None:
            card.refresh()
        if not self._cardRefreshTimer.isActive():
            self._cardRefreshTimer.start()
        self._refreshListTimer.start()

    def _onTaskStopped(self, task: Task) -> None:
        self._tickingIds.discard(task.taskId)
        card = self._liveCards.get(task.taskId)
        if card is not None:
            card.refresh()
        if not self._tickingIds:
            self._cardRefreshTimer.stop()
        self._refreshListTimer.start()

    def _onTickingStarted(self, task: Task) -> None:
        self._tickingIds.add(task.taskId)
        card = self._liveCards.get(task.taskId)
        if card is not None:
            card.refresh()
        if not self._cardRefreshTimer.isActive():
            self._cardRefreshTimer.start()

    def _onTickingStopped(self, task: Task) -> None:
        self._tickingIds.discard(task.taskId)
        card = self._liveCards.get(task.taskId)
        if card is not None:
            card.refresh(force=True)
        if not self._tickingIds:
            self._cardRefreshTimer.stop()

    def _onChecksumCompleted(self, task: Task, _algorithm: str) -> None:
        self._onTickingStopped(task)
        if self.isVisible() and QApplication.activeModalWidget() is None:
            from app.view.dialogs.task_drawer import TaskDrawer
            TaskDrawer(task, self._taskService, self._featureService, self._categoryService, self.window()).show()

    def _onChecksumStopped(self, task: Task, error) -> None:
        self._onTickingStopped(task)
        if error is not None:
            InfoBar.error(
                self.tr("校验失败"),
                toLocalizedError(error),
                duration=5000,
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

    # ── band selection ──

    def _onBandDragStarted(self, modifiers: Qt.KeyboardModifiers) -> None:
        if not self._isSelectionMode:
            self.setSelectionMode(True)
        keepExisting = modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)
        self._bandSnapshot = set(self._selectedIds) if keepExisting else set()
        self._bandMerge = set.__xor__ if modifiers & Qt.KeyboardModifier.ControlModifier else set.__or__

    def _onBandChanged(self, first: int, last: int) -> None:
        bandIds = {self._displayOrder[i] for i in range(first, last + 1)} if first >= 0 else set()
        self._selectedIds = self._bandMerge(self._bandSnapshot, bandIds)
        for taskId, card in self._liveCards.items():
            card.setChecked(taskId in self._selectedIds)

    def _onBandDragFinished(self) -> None:
        if not self._selectedIds:
            self.setSelectionMode(False)

    # ── file drag ──

    def _onCardDragRequested(self, taskId: str) -> None:
        from app.platform.desktop import startFileDrag
        if self._isSelectionMode and taskId in self._selectedIds:
            paths = [
                Path(task.outputPath)
                for tid in self._selectedIds
                if (task := self._taskService.taskById(tid))
                and task.status == TaskStatus.COMPLETED
                and task.hasOutputFile
                and isExisting(task.outputPath)
            ]
        else:
            task = self._taskService.taskById(taskId)
            paths = [Path(task.outputPath)] if task else []
        if paths:
            startFileDrag(paths, self)

    # ── events ──

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.SelectAll) and self._displayOrder:
            self.setSelectionMode(True)
            self.selectAll()
        elif event.key() == Qt.Key.Key_Delete and self._isSelectionMode:
            self._onDeleteSelected()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refreshViewport()
        self.commandView.move(
            (self.width() - self.commandView.width()) // 2,
            self.height() - self.commandView.sizeHint().height() - 20,
        )
        self.emptyStatusWidget.move(
            (self.width() - self.emptyStatusWidget.width()) // 2,
            (self.height() - self.emptyStatusWidget.height()) // 2,
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refreshViewport()

    def _createCard(self, task: Task) -> TaskCard | None:
        return self._featureService.taskCard(task, self.scrollWidget)

    def _onTaskAdded(self, task: Task) -> None:
        self._refreshListTimer.start()

    def _onTaskRemoved(self, taskId: str) -> None:
        card = self._liveCards.pop(taskId, None)
        if card is not None:
            self._unmountCard(card)
        self._selectedIds.discard(taskId)
        self._tickingIds.discard(taskId)
        if self._selectionAnchor == taskId:
            self._selectionAnchor = None
        if not self._tickingIds:
            self._cardRefreshTimer.stop()
        self._refreshListTimer.start()

    def _onFileDisappeared(self, task: Task) -> None:
        card = self._liveCards.get(task.taskId)
        if card is not None:
            card.refresh(force=True)

    def _onOverwriteFailed(self, task: Task) -> None:
        InfoBar.warning(
            self.tr("无法移到回收站"),
            self.tr("旧文件已保留，新文件另存为 {0}").format(task.name),
            duration=5000,
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )

    def _onEditFailed(self, task: Task, error: TaskError) -> None:
        InfoBar.error(
            self.tr("无法修改任务"),
            toLocalizedError(error),
            duration=5000,
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )

    def _onFileDeleteDenied(self) -> None:
        bar = InfoBar.warning(
            self.tr("文件删除受限"),
            self.tr("macOS 阻止了文件删除，请在 系统设置 > 隐私与安全性 > 完全磁盘访问权限 中添加 Ghost Downloader"),
            duration=-1,
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )
        button = PushButton(self.tr("前往设置"))
        button.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")
        ))
        bar.addWidget(button)
