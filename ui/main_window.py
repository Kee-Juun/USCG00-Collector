from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QThread,
    Qt,
    QUrl,
    pyqtProperty,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QCloseEvent,
    QDesktopServices,
    QFontDatabase,
    QLinearGradient,
    QMouseEvent,
    QPainter,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.collector import CollectionBlocked, Collector, resolve_output_root
from utils.logger import build_logger


class SheenProgressBar(QProgressBar):
    """Progress bar with a subtle moving highlight while work is active."""

    def __init__(self):
        super().__init__()
        self._sheen_position = -0.25
        self._sheen_active = False
        self._sheen_animation = QPropertyAnimation(self, b"sheenPosition", self)
        self._sheen_animation.setStartValue(-0.25)
        self._sheen_animation.setEndValue(1.25)
        self._sheen_animation.setDuration(1250)
        self._sheen_animation.setLoopCount(-1)
        self._sheen_animation.setEasingCurve(QEasingCurve.Type.Linear)

    @pyqtProperty(float)
    def sheenPosition(self) -> float:
        return self._sheen_position

    @sheenPosition.setter
    def sheenPosition(self, value: float) -> None:
        self._sheen_position = value
        self.update()

    def start_sheen(self) -> None:
        self._sheen_active = True
        self._sheen_animation.start()

    def stop_sheen(self) -> None:
        self._sheen_active = False
        self._sheen_animation.stop()
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._sheen_active:
            return
        span = max(1, self.maximum() - self.minimum())
        progress = max(0.0, min(1.0, (self.value() - self.minimum()) / span))
        fill_width = int(self.width() * progress) if progress > 0 else self.width()
        center = self._sheen_position * self.width()
        gradient = QLinearGradient(center - 34, 0, center + 34, 0)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
        gradient.setColorAt(0.5, QColor(255, 255, 255, 150))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter = QPainter(self)
        painter.setClipRect(0, 0, fill_width, self.height())
        painter.fillRect(self.rect(), gradient)


class CollectionWorker(QObject):
    status_message = pyqtSignal(str)
    run_folder_ready = pyqtSignal(object)
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object, object)
    failed = pyqtSignal(str, bool, object)

    def __init__(self, config: dict, year: int):
        super().__init__()
        self.config = config
        self.year = year

    def _receive_log(self, message: str) -> None:
        if message.startswith("Run folder ready:"):
            self.run_folder_ready.emit(Path(message.split(":", 1)[1].strip()))
        status = friendly_status(message)
        if status:
            self.status_message.emit(status)

    @pyqtSlot()
    def run(self) -> None:
        logger, _ = build_logger(sink=self._receive_log)
        collector = Collector(self.config, logger)
        try:
            run_dir, results = collector.collect(
                self.year,
                lambda done, total: self.progress.emit(done, total),
            )
            self.finished.emit(run_dir, results)
        except Exception as exc:
            self.failed.emit(str(exc), isinstance(exc, CollectionBlocked), collector.run_dir)


def friendly_status(message: str) -> str:
    """Map the detailed file log to a few useful UI milestones."""
    if message.startswith("Run folder ready:"):
        return "Preparing the collection run…"
    if message.startswith("SF-View index ready:"):
        return "Duplicate check ready. Loading USCG decisions…"
    if message.startswith("USCG ") and "downloadable decisions" in message:
        try:
            count = message.split("contains ", 1)[1].split(" downloadable", 1)[0]
            return f"Found {count} downloadable decisions."
        except IndexError:
            return "USCG decisions loaded."
    return ""


class TitleBar(QFrame):
    """Small draggable title bar for the frameless window."""

    def __init__(self, window: "CollectorWindow"):
        super().__init__(objectName="titleBar")
        self.host = window
        self.drag_offset: QPoint | None = None
        self.setFixedHeight(46)

        row = QHBoxLayout(self)
        row.setContentsMargins(18, 7, 8, 7)
        row.setSpacing(9)

        row.addWidget(QLabel("USCG00 Collector", objectName="title"), 1)

        minimize = QPushButton("−", objectName="windowButton")
        minimize.setToolTip("Minimize")
        minimize.clicked.connect(window.showMinimized)
        row.addWidget(minimize)

        close = QPushButton("×", objectName="closeButton")
        close.setToolTip("Close")
        close.clicked.connect(window.close)
        row.addWidget(close)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.host.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.host.move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.drag_offset = None
        event.accept()


class CollectorWindow(QMainWindow):
    def __init__(self, config: dict, project_root: Path):
        super().__init__()
        self.config = config
        self.project_root = project_root
        self.thread: QThread | None = None
        self.worker: CollectionWorker | None = None
        self.last_run_dir: Path | None = None
        self.setWindowTitle("USCG00 Collector")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowSystemMenuHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(500, 246)
        self._build_ui()
        asset_dir = (self.project_root / "assets").resolve()
        title_font_family = "Segoe UI"
        font_id = QFontDatabase.addApplicationFont(str(asset_dir / "Neogen-Black.ttf"))
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                title_font_family = families[0]
        style_sheet = STYLE_SHEET.replace(
            "__CHEVRON_UP__", (asset_dir / "chevron-up.svg").as_posix()
        ).replace(
            "__CHEVRON_DOWN__", (asset_dir / "chevron-down.svg").as_posix()
        ).replace(
            "__TITLE_FONT__", title_font_family
        )
        self.setStyleSheet(style_sheet)

    def _build_ui(self) -> None:
        central = QWidget(objectName="transparentRoot")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)

        shell = QFrame(objectName="windowShell")
        outer.addWidget(shell)

        page = QVBoxLayout(shell)
        page.setContentsMargins(0, 0, 0, 10)
        page.setSpacing(8)
        page.addWidget(TitleBar(self))

        content = QVBoxLayout()
        content.setContentsMargins(12, 0, 12, 0)
        content.setSpacing(8)

        settings = QFrame(objectName="card")
        settings.setFixedHeight(54)
        settings_row = QHBoxLayout(settings)
        settings_row.setContentsMargins(14, 8, 14, 8)
        settings_row.setSpacing(12)

        settings_row.addStretch(1)
        settings_row.addWidget(QLabel("DECISION YEAR", objectName="fieldLabel"))
        self.year_input = QSpinBox()
        self.year_input.setRange(1990, 2100)
        self.year_input.setValue(int(self.config["default_year"]))
        self.year_input.setFixedSize(104, 34)
        settings_row.addWidget(self.year_input)
        settings_row.addStretch(1)
        content.addWidget(settings)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        self.start_button = QPushButton("COLLECT DECISIONS", objectName="primaryButton")
        self.start_button.clicked.connect(self._start_collection)
        self.open_button = QPushButton("VIEW FOLDER", objectName="secondaryButton")
        self.open_button.setFixedWidth(126)
        self.open_button.clicked.connect(self._open_latest_run)
        buttons.addWidget(self.start_button, 1)
        buttons.addWidget(self.open_button)
        content.addLayout(buttons)

        self.progress_bar = SheenProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        content.addWidget(self.progress_bar)

        status_card = QFrame(objectName="statusCard")
        status_card.setFixedHeight(45)
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(12, 7, 12, 7)
        self.status_label = QLabel(
            f"Ready to collect {self.year_input.value()} decisions.",
            objectName="statusText",
        )
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setToolTip(self.status_label.text())
        status_layout.addWidget(self.status_label)
        content.addWidget(status_card)

        self.year_input.valueChanged.connect(
            lambda year: self._set_status(f"Ready to collect {year} decisions.")
        )
        page.addLayout(content)

    def _start_collection(self) -> None:
        self.last_run_dir = None
        self._set_running(True)
        self.progress_bar.setValue(0)
        self._set_status("Preparing the collection run…")

        self.thread = QThread(self)
        self.worker = CollectionWorker(dict(self.config), self.year_input.value())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.status_message.connect(self._set_status)
        self.worker.run_folder_ready.connect(self._set_run_dir)
        self.worker.progress.connect(self._update_progress)
        self.worker.finished.connect(self._collection_finished)
        self.worker.failed.connect(self._collection_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.start()

    @pyqtSlot(str)
    def _set_status(self, message: str) -> None:
        metrics = self.status_label.fontMetrics()
        self.status_label.setText(metrics.elidedText(message, Qt.TextElideMode.ElideRight, 440))
        self.status_label.setToolTip(message)

    @pyqtSlot(int, int)
    def _update_progress(self, done: int, total: int) -> None:
        self.progress_bar.setValue(round((done / total) * 100) if total else 0)
        self._set_status(f"Validating decisions and preparing filenames  •  {done} / {total}")

    @pyqtSlot(object, object)
    def _collection_finished(self, run_dir: object, results: object) -> None:
        self.last_run_dir = Path(run_dir)
        items = list(results)
        saved = sum(item.status == "SAVED" for item in items)
        excluded = sum(item.status == "EXCLUDED_NON_ORDER" for item in items)
        skipped = sum(item.status.startswith("SKIPPED") for item in items)
        errors = sum(item.status == "ERROR" for item in items)
        self.progress_bar.setValue(100)
        self._set_status(
            f"Done  •  {saved} saved  •  {excluded} excluded  •  {skipped} duplicates  •  {errors} errors"
        )
        self._set_running(False)
        self._show_result_dialog(
            QMessageBox.Icon.Information,
            "Collection complete",
            f"Saved {saved} decisions.",
        )

    @pyqtSlot(str, bool, object)
    def _collection_failed(self, message: str, blocked: bool, run_dir: object) -> None:
        if run_dir:
            self.last_run_dir = Path(run_dir)
        self._set_status("Collection blocked." if blocked else "Collection failed.")
        self._set_running(False)
        self._show_result_dialog(
            QMessageBox.Icon.Critical,
            "Collection blocked" if blocked else "Collection failed",
            message,
        )

    def _show_result_dialog(self, icon: QMessageBox.Icon, title: str, message: str) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(icon)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setInformativeText("Click OK to open the run folder.")
        ok_button = dialog.addButton(QMessageBox.StandardButton.Ok)
        dialog.exec()
        if dialog.clickedButton() is ok_button:
            self._open_latest_run()
            self._reset_for_next_run()

    def _reset_for_next_run(self) -> None:
        self.progress_bar.stop_sheen()
        self.progress_bar.setValue(0)
        self._set_status(f"Ready to collect {self.year_input.value()} decisions.")

    @pyqtSlot(object)
    def _set_run_dir(self, run_dir: object) -> None:
        self.last_run_dir = Path(run_dir)

    def _thread_finished(self) -> None:
        self.thread = None
        self.worker = None

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.year_input.setEnabled(not running)
        self.start_button.setText("WORKING…" if running else "COLLECT DECISIONS")
        if running:
            self.progress_bar.start_sheen()
        else:
            self.progress_bar.stop_sheen()

    def _open_latest_run(self) -> None:
        target = self.last_run_dir or resolve_output_root(self.config)
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.information(
                self,
                "Collection in progress",
                "Please let the current run finish before closing.",
            )
            event.ignore()
            return
        event.accept()


STYLE_SHEET = """
QWidget#transparentRoot { background: transparent; }
QFrame#windowShell {
    background: #090f1a;
    border: 1px solid #2a3a4f;
    border-radius: 15px;
}
QFrame#titleBar {
    background: #0d1726;
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
}
QWidget {
    color: #e7eef7;
    font-family: "Segoe UI";
    font-size: 9pt;
}
QLabel { background: transparent; }
QLabel#title {
    color: #f4f8fc;
    font-family: "__TITLE_FONT__";
    font-size: 13pt;
    font-weight: normal;
}
QPushButton#windowButton, QPushButton#closeButton {
    background: transparent;
    color: #9db0c5;
    border: none;
    border-radius: 8px;
    padding: 0;
    min-height: 28px;
    min-width: 38px;
    max-width: 38px;
    font-size: 14pt;
    font-weight: 500;
}
QPushButton#windowButton:hover { background: #20344b; color: white; }
QPushButton#closeButton:hover { background: #d9525e; color: white; }
QFrame#card, QFrame#statusCard {
    background: #111c2b;
    border: 1px solid #26384d;
    border-radius: 10px;
}
QLabel#fieldLabel { color: #8fa4ba; font-size: 7.5pt; font-weight: 800; }
QSpinBox {
    background: #182639;
    color: #f2f7fb;
    border: 1px solid #38506a;
    border-radius: 7px;
    padding: 4px 7px;
    selection-background-color: #19a999;
    font-weight: 700;
}
QSpinBox:focus { background: #1b2b40; border-color: #20c2af; }
QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    background: #203249;
    border: none;
    border-left: 1px solid #38506a;
    border-bottom: 1px solid #2d4259;
    border-top-right-radius: 6px;
    width: 24px;
    height: 16px;
}
QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    background: #203249;
    border: none;
    border-left: 1px solid #38506a;
    border-bottom-right-radius: 6px;
    width: 24px;
    height: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #2b425d; }
QSpinBox::up-arrow {
    image: url("__CHEVRON_UP__");
    width: 10px;
    height: 6px;
}
QSpinBox::down-arrow {
    image: url("__CHEVRON_DOWN__");
    width: 10px;
    height: 6px;
}
QPushButton#primaryButton, QPushButton#secondaryButton {
    border-radius: 9px;
    padding: 7px 12px;
    min-height: 22px;
    font-weight: 750;
}
QPushButton#primaryButton { background: #19ad9d; color: white; border: none; }
QPushButton#primaryButton:hover { background: #20c2af; }
QPushButton#primaryButton:pressed { background: #118577; }
QPushButton#primaryButton:disabled { background: #285a5a; color: #8fb7b4; }
QPushButton#secondaryButton {
    background: #152235;
    color: #d8e4ef;
    border: 1px solid #344a62;
}
QPushButton#secondaryButton:hover { background: #20344a; border-color: #4a6783; }
QPushButton#secondaryButton:disabled { color: #64788c; background: #101a28; }
QProgressBar { background: #182638; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #1bb7a5; border-radius: 3px; }
QLabel#statusText { color: #dce7f2; font-weight: 650; }
QMessageBox {
    background: #101927;
    color: #eef5fb;
}
QMessageBox QLabel {
    background: transparent;
    color: #eef5fb;
}
QMessageBox QLabel#qt_msgbox_label { min-width: 220px; }
QMessageBox QPushButton {
    background: #1aab9b;
    color: white;
    border: 1px solid #2bc0af;
    border-radius: 7px;
    min-width: 82px;
    min-height: 28px;
    padding: 2px 12px;
    font-weight: 700;
}
QMessageBox QPushButton:hover { background: #20c2af; }
"""


def run_app(config: dict, project_root: Path) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("USCG00 Collector")
    app.setStyle("Fusion")
    window = CollectorWindow(config, project_root)
    window.show()
    return app.exec()
