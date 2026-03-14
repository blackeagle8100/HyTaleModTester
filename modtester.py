#!/usr/bin/env python3
import sys
import os
import re
import glob
import json
import subprocess
from pathlib import Path


def ensure_pyqt6():
    try:
        import PyQt6  # noqa: F401
        return
    except ImportError:
        print("PyQt6 not found. Trying to install it with yay...")

        try:
            subprocess.run(
                ["yay", "-S", "--noconfirm", "python-pyqt6"],
                check=True
            )
        except FileNotFoundError:
            print("yay was not found.")
            print("Install it manually with:")
            print("  sudo pacman -S python-pyqt6")
            sys.exit(1)
        except subprocess.CalledProcessError:
            print("Installation via yay failed.")
            print("Try manually:")
            print("  yay -S python-pyqt6")
            sys.exit(1)

        print("PyQt6 installed. Restarting script...")
        os.execv(sys.executable, [sys.executable] + sys.argv)


ensure_pyqt6()

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QMessageBox,
    QGroupBox,
)


DEFAULT_LOG_DIR = os.path.expanduser("~/.local/share/Hytale/UserData/Logs")
DEFAULT_WORLD_CONFIG = os.path.expanduser("~/.local/share/Hytale/UserData/Saves/Lemmia/config.json")


def find_latest_client_log(log_dir: str) -> str | None:
    files = glob.glob(os.path.join(log_dir, "*client.log"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def read_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception as e:
        return [f"[ERROR] Could not read file: {e}\n"]


def extract_recent_relevant(lines: list[str], mod_id: str, max_lines: int = 120) -> list[str]:
    """
    Extract lines containing the mod id or relevant plugin/warning/error info.
    """
    mod_id_lower = mod_id.lower()
    short_name = mod_id.split(":")[-1].lower() if ":" in mod_id else mod_id.lower()

    result = []
    for line in lines:
        l = line.lower()
        if (
            mod_id_lower in l
            or short_name in l
            or "enabled plugin" in l
            or "failed to setup plugin" in l
            or "skipping mod" in l
            or "loaded pack" in l
            or "severe" in l
            or "exception" in l
            or "caused by" in l
            or "lacking dependency" in l
        ):
            result.append(line.rstrip())

    return result[-max_lines:]


def extract_warning_error_block(lines: list[str], mod_id: str, context: int = 8, max_blocks: int = 12) -> list[str]:
    """
    Find warnings/errors around the mod and include context so it's easy to copy-paste.
    """
    mod_id_lower = mod_id.lower()
    short_name = mod_id.split(":")[-1].lower() if ":" in mod_id else mod_id.lower()

    match_indexes = []
    for i, line in enumerate(lines):
        l = line.lower()
        if (
            mod_id_lower in l
            or short_name in l
            or "failed to setup plugin" in l
            or "lacking dependency" in l
            or "enabled plugin" in l
            or "skipping mod" in l
            or "loaded pack" in l
        ):
            match_indexes.append(i)

    blocks = []
    used_ranges = []

    def overlaps(a1, a2, b1, b2):
        return not (a2 < b1 or b2 < a1)

    for idx in match_indexes:
        start = max(0, idx - context)
        end = min(len(lines) - 1, idx + context)

        is_err_block = False
        for j in range(start, end + 1):
            lj = lines[j].lower()
            if any(x in lj for x in ["warn", "error", "severe", "exception", "caused by", "failed"]):
                is_err_block = True
                break

        if not is_err_block:
            continue

        skip = False
        for r1, r2 in used_ranges:
            if overlaps(start, end, r1, r2):
                skip = True
                break
        if skip:
            continue

        used_ranges.append((start, end))
        header = f"--- block around line {idx + 1} ---"
        body = [lines[j].rstrip() for j in range(start, end + 1)]
        blocks.append(header)
        blocks.extend(body)
        blocks.append("")

        if len(blocks) >= max_blocks * (context * 2 + 3):
            break

    return blocks[:2000]


def determine_status(lines: list[str], mod_id: str) -> tuple[str, str]:
    text = "\n".join(lines).lower()
    mid = mod_id.lower()
    short_name = mod_id.split(":")[-1].lower() if ":" in mod_id else mid

    def has(pattern: str) -> bool:
        return re.search(pattern, text, re.IGNORECASE) is not None

    if has(rf"enabled plugin {re.escape(mod_id)}\b"):
        return "ACTIVE", "This mod appears to be loaded correctly and enabled."
    if has(rf"loaded pack: {re.escape(mod_id)}\b"):
        return "LOADED AS PACK", "Assets were loaded, but no 'Enabled plugin' line was found."
    if has(rf"skipping mod {re.escape(mod_id)} \(disabled by server config\)"):
        return "DISABLED", "The mod is disabled in the world config."
    if has(rf"failed to setup plugin {re.escape(mod_id)}\b") or short_name in text and "failed to setup plugin" in text:
        return "CRASHED DURING STARTUP", "The mod tries to start but fails during setup."
    if has(rf"{re.escape(mod_id)} is lacking dependency"):
        return "DEPENDENCY ISSUE", "The mod is missing a dependency/module during setup."
    if mid in text or short_name in text:
        return "FOUND IN LOG", "The mod appears in the log, but the final status is not fully clear."
    return "NOT FOUND", "No relevant lines were found in this log."


def set_mod_enabled(config_path: str, mod_id: str, enabled: bool) -> tuple[bool, str]:
    path = Path(config_path)
    if not path.exists():
        return False, f"Config not found: {config_path}"

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return False, f"Could not read config: {e}"

    mods = data.get("Mods")
    if not isinstance(mods, dict):
        return False, "Config does not contain a valid 'Mods' section."

    if mod_id not in mods:
        return False, f"Mod not found in config: {mod_id}"

    if not isinstance(mods[mod_id], dict):
        mods[mod_id] = {}

    mods[mod_id]["Enabled"] = enabled

    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except Exception as e:
        return False, f"Could not write config: {e}"

    return True, f"{mod_id} -> Enabled={enabled}"


class ModTester(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hytale Mod Tester")
        self.resize(1100, 850)

        self.log_dir = DEFAULT_LOG_DIR
        self.config_path = DEFAULT_WORLD_CONFIG

        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)

        # Mod input
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Mod ID:"))
        self.mod_input = QLineEdit()
        self.mod_input.setPlaceholderText("Example: Gnarly:GnarlyGliders or DragoKane:GrapplingHookMod")
        row1.addWidget(self.mod_input)
        layout.addLayout(row1)

        # Log dir
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Log folder:"))
        self.log_dir_input = QLineEdit(self.log_dir)
        row2.addWidget(self.log_dir_input)
        browse_logs_btn = QPushButton("Browse")
        browse_logs_btn.clicked.connect(self.browse_logs)
        row2.addWidget(browse_logs_btn)
        layout.addLayout(row2)

        # Config path
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("World config:"))
        self.config_input = QLineEdit(self.config_path)
        row3.addWidget(self.config_input)
        browse_cfg_btn = QPushButton("Browse")
        browse_cfg_btn.clicked.connect(self.browse_config)
        row3.addWidget(browse_cfg_btn)
        layout.addLayout(row3)

        # Buttons
        row4 = QHBoxLayout()
        self.test_btn = QPushButton("Test mod")
        self.test_btn.clicked.connect(self.test_mod)
        row4.addWidget(self.test_btn)

        self.enable_btn = QPushButton("Enable in config")
        self.enable_btn.clicked.connect(self.enable_mod)
        row4.addWidget(self.enable_btn)

        self.disable_btn = QPushButton("Disable in config")
        self.disable_btn.clicked.connect(self.disable_mod)
        row4.addWidget(self.disable_btn)

        layout.addLayout(row4)

        # Status
        status_box = QGroupBox("Status")
        status_layout = QVBoxLayout()
        self.status_label = QLabel("Nothing tested yet.")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        status_layout.addWidget(self.status_label)
        status_box.setLayout(status_layout)
        layout.addWidget(status_box)

        # Relevant lines
        relevant_box = QGroupBox("Relevant log lines")
        relevant_layout = QVBoxLayout()
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        relevant_layout.addWidget(self.output)
        relevant_box.setLayout(relevant_layout)
        layout.addWidget(relevant_box, 2)

        # warnings/errors
        warn_box = QGroupBox("Warnings / Errors for easy copy-paste")
        warn_layout = QVBoxLayout()
        self.warn_output = QTextEdit()
        self.warn_output.setReadOnly(True)
        self.warn_output.setPlaceholderText("Warnings and errors with context will appear here.")
        warn_layout.addWidget(self.warn_output)
        warn_box.setLayout(warn_layout)
        layout.addWidget(warn_box, 3)

    def browse_logs(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose log folder", self.log_dir_input.text())
        if folder:
            self.log_dir_input.setText(folder)

    def browse_config(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose world config.json",
            self.config_input.text() or os.path.expanduser("~"),
            "JSON Files (*.json);;All Files (*)"
        )
        if file_path:
            self.config_input.setText(file_path)

    def current_mod_id(self) -> str:
        return self.mod_input.text().strip()

    def set_status(self, title: str, detail: str):
        self.status_label.setText(f"<b>{title}</b><br>{detail}")

    def enable_mod(self):
        mod_id = self.current_mod_id()
        if not mod_id:
            QMessageBox.warning(self, "No mod", "Please enter a mod ID first.")
            return

        ok, msg = set_mod_enabled(self.config_input.text().strip(), mod_id, True)
        if ok:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.critical(self, "Error", msg)

    def disable_mod(self):
        mod_id = self.current_mod_id()
        if not mod_id:
            QMessageBox.warning(self, "No mod", "Please enter a mod ID first.")
            return

        ok, msg = set_mod_enabled(self.config_input.text().strip(), mod_id, False)
        if ok:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.critical(self, "Error", msg)

    def test_mod(self):
        mod_id = self.current_mod_id()
        log_dir = self.log_dir_input.text().strip()

        if not mod_id:
            QMessageBox.warning(self, "No mod", "Please enter a mod ID first.")
            return

        latest_log = find_latest_client_log(log_dir)
        if not latest_log:
            self.set_status("No log found", f"No *client.log found in:\n{log_dir}")
            self.output.clear()
            self.warn_output.clear()
            return

        lines = read_lines(latest_log)

        status, detail = determine_status(lines, mod_id)
        self.set_status(status, f"{detail}<br><br><b>Log:</b> {latest_log}")

        relevant = extract_recent_relevant(lines, mod_id)
        if relevant:
            self.output.setPlainText("\n".join(relevant))
        else:
            self.output.setPlainText("No relevant lines found.")

        warn_blocks = extract_warning_error_block(lines, mod_id)
        if warn_blocks:
            self.warn_output.setPlainText("\n".join(warn_blocks))
        else:
            self.warn_output.setPlainText("No warnings/errors found for this mod in this log.")


def main():
    app = QApplication(sys.argv)
    win = ModTester()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
