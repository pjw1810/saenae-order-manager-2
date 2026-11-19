import sys, os, json, sqlite3, threading, socket
from pathlib import Path
from datetime import date, datetime, timedelta
import shutil
import threading
import uuid
import hashlib
import ctypes
import shlex
import subprocess
from flask import Flask, request, jsonify
from werkzeug.serving import make_server
import requests
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRunnable, QThreadPool, QDate, QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QDialog, QFormLayout, QSpinBox, QDateEdit, QComboBox, QFileDialog, QCheckBox, QFrame, QHeaderView
)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from version import APP_VERSION

APP_NAME = "Order Manager"
PORT = 8765
BASE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SaenaeOrderManager"
BASE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "orders.sqlite3"
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_LOCK = threading.Lock()
UPDATE_CONFIG_PATH = BASE_DIR / "update_config.json"
UPDATE_DIR = BASE_DIR / "updates"


def load_update_config():
    try:
        return json.loads(UPDATE_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"github_repo": "OWNER/REPOSITORY", "release_api": "https://api.github.com/repos/{github_repo}/releases/latest"}


def version_tuple(value):
    parts = []
    for token in str(value).lstrip("vV").split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple((parts + [0, 0, 0])[:3])

DEFAULT_CONFIG = {"server_mode": False, "server_host": "127.0.0.1", "port": PORT, "auto_start": False}

def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {**DEFAULT_CONFIG, **data}
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(config):
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

def set_windows_autostart(enabled):
    if os.name != "nt":
        return
    import winreg
    run_key = r"Software\\Microsoft\\Windows\\CurrentVersion\\Run"
    app_path = sys.executable
    # PyInstaller 실행 파일이면 sys.executable이 실제 EXE 경로입니다.
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "SaenaeOrderManager", 0, winreg.REG_SZ, f'"{app_path}"')
        else:
            try:
                winreg.DeleteValue(key, "SaenaeOrderManager")
            except FileNotFoundError:
                pass

def db_connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con

def backup_database():
    """Create a consistent SQLite snapshot; close both DB handles before rename."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # Prevent the timer, manual backup, and shutdown backup from overlapping.
    with BACKUP_LOCK:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = BACKUP_DIR / f"orders_{stamp}.sqlite3"
