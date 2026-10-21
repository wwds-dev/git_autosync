"""Dialog for configuring the background auto-sync schedule (a launchd
LaunchAgent). Exposes every option relevant to a scheduled sync: how often
(or what time of day), and whether to run once immediately when enabled."""
from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from . import paths, scheduler

INTERVAL_PRESETS = [
    ("15 minutes", 15 * 60),
    ("30 minutes", 30 * 60),
    ("1 hour", 60 * 60),
    ("6 hours", 6 * 60 * 60),
    ("12 hours", 12 * 60 * 60),
    ("24 hours", 24 * 60 * 60),
]


class ScheduleDialog(QDialog):
    def __init__(self, parent, config_path):
        super().__init__(parent)
        self.setWindowTitle("Schedule auto-sync")
        self.resize(460, 360)
        self.config_path = config_path

        self._build_ui()
        self._load_current_schedule()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "background:#e2e3e5; color:#41464b; padding:8px; border-radius:4px;"
        )
        layout.addWidget(self.status_label)

        self.gh_banner = QLabel()
        self.gh_banner.setWordWrap(True)
        self.gh_banner.setStyleSheet(
            "background:#fff3cd; color:#664d03; padding:8px; border-radius:4px;"
        )
        self.gh_banner.hide()
        layout.addWidget(self.gh_banner)

        mode_row = QHBoxLayout()
        self.interval_radio = QRadioButton("Run every…")
        self.calendar_radio = QRadioButton("Run daily at…")
        self.interval_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.interval_radio)
        group.addButton(self.calendar_radio)
        self.interval_radio.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.interval_radio)
        mode_row.addWidget(self.calendar_radio)
        layout.addLayout(mode_row)

        self.stack = QStackedWidget()

        interval_page = QWidget()
        interval_form = QFormLayout(interval_page)
        self.interval_combo = QComboBox()
        for label, _ in INTERVAL_PRESETS:
            self.interval_combo.addItem(label)
        self.interval_combo.setCurrentIndex(3)  # 6 hours, the existing default
        interval_form.addRow("Frequency:", self.interval_combo)
        self.stack.addWidget(interval_page)

        calendar_page = QWidget()
        calendar_form = QFormLayout(calendar_page)
        self.time_edit = QTimeEdit(QTime(3, 30))
        self.time_edit.setDisplayFormat("HH:mm")
        calendar_form.addRow("Time of day:", self.time_edit)
        self.stack.addWidget(calendar_page)

        layout.addWidget(self.stack)

        form2 = QFormLayout()
        self.run_at_load_combo = QComboBox()
        self.run_at_load_combo.addItems(["No — wait for the first scheduled time", "Yes — also run once now"])
        form2.addRow("Run immediately when enabled:", self.run_at_load_combo)
        layout.addLayout(form2)

        # Independent of the sync schedule above: its own LaunchAgent.
        # Short label: a QCheckBox cannot wrap, and a long one sets the
        # dialog's minimum width. The detail is in the tooltip.
        self.hourly_box = QCheckBox("Also back up every hour")
        self.hourly_box.setToolTip(
            "Every hour, branches and uncommitted work go to the private backup "
            "repo. Nothing is committed, and nothing is pushed to the projects' "
            "own GitHub repos; that stays with the schedule above.")
        self.hourly_box.setChecked(scheduler.is_hourly_backup_installed())
        self.hourly_box.toggled.connect(self._on_hourly_toggled)
        layout.addWidget(self.hourly_box)

        layout.addStretch(1)

        buttons_row = QHBoxLayout()
        self.enable_btn = QPushButton("Enable / Update Schedule")
        self.enable_btn.clicked.connect(self._on_enable)
        self.disable_btn = QPushButton("Disable Schedule")
        self.disable_btn.clicked.connect(self._on_disable)
        buttons_row.addWidget(self.enable_btn)
        buttons_row.addWidget(self.disable_btn)
        layout.addLayout(buttons_row)

        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.rejected.connect(self.reject)
        close_buttons.button(QDialogButtonBox.Close).clicked.connect(self.reject)
        layout.addWidget(close_buttons)

    def _on_hourly_toggled(self, on: bool):
        try:
            if on:
                scheduler.install_hourly_backup(self.config_path)
            else:
                scheduler.uninstall_hourly_backup()
        except Exception as e:   # launchctl refused: say so, show the real state
            QMessageBox.warning(self, "Hourly backup", f"Could not change the hourly backup: {e}")
            self.hourly_box.blockSignals(True)
            self.hourly_box.setChecked(scheduler.is_hourly_backup_installed())
            self.hourly_box.blockSignals(False)

    def _on_mode_changed(self, interval_checked: bool):
        self.stack.setCurrentIndex(0 if interval_checked else 1)

    def _load_current_schedule(self):
        if not paths.find_gitleaks():
            self.gh_banner.setText(
                "gitleaks not found. Install it before enabling a schedule:\n"
                "brew install gitleaks"
            )
            self.gh_banner.show()
            self.enable_btn.setEnabled(False)

        schedule = scheduler.get_schedule()
        if not schedule:
            self.status_label.setText("Current status: not scheduled.")
            self.disable_btn.setEnabled(False)
            return

        if schedule["mode"] == "interval":
            seconds = schedule["interval_seconds"]
            label = next((l for l, s in INTERVAL_PRESETS if s == seconds), f"{seconds} seconds")
            self.interval_radio.setChecked(True)
            idx = self.interval_combo.findText(label)
            if idx >= 0:
                self.interval_combo.setCurrentIndex(idx)
            self.status_label.setText(f"Current status: scheduled — every {label}.")
        else:
            self.calendar_radio.setChecked(True)
            self.time_edit.setTime(QTime(schedule["hour"], schedule["minute"]))
            self.status_label.setText(
                f"Current status: scheduled — daily at {schedule['hour']:02d}:{schedule['minute']:02d}."
            )
        self.run_at_load_combo.setCurrentIndex(1 if schedule.get("run_at_load") else 0)

    def _on_enable(self):
        run_at_load = self.run_at_load_combo.currentIndex() == 1
        if self.interval_radio.isChecked():
            _, seconds = INTERVAL_PRESETS[self.interval_combo.currentIndex()]
            description = f"every {self.interval_combo.currentText()}"
        else:
            t = self.time_edit.time()
            description = f"daily at {t.toString('HH:mm')}"

        extra = " It will also run once immediately." if run_at_load else ""
        reply = QMessageBox.question(
            self,
            "Confirm schedule",
            f"This installs a background job that runs autosync {description}, "
            f"even when this app is closed, and pushes any clean repo with changes "
            f"(the same leak-gate still applies).{extra} Continue?",
        )
        if reply != QMessageBox.Yes:
            return

        if self.interval_radio.isChecked():
            scheduler.install_interval(seconds, self.config_path, run_at_load=run_at_load)
        else:
            t = self.time_edit.time()
            scheduler.install_calendar(t.hour(), t.minute(), self.config_path, run_at_load=run_at_load)

        self._load_current_schedule()
        self.disable_btn.setEnabled(True)

    def _on_disable(self):
        scheduler.uninstall()
        self._load_current_schedule()
