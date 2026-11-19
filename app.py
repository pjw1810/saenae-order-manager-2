# Startup diagnostics before third-party imports.
import os as _diag_os
import sys as _diag_sys
import logging as _diag_logging
import tempfile as _diag_tempfile
try:
    _diag_base = _diag_os.path.join(_diag_os.environ.get("LOCALAPPDATA", _diag_tempfile.gettempdir()), "SaenaeOrderManager")
    _diag_os.makedirs(_diag_base, exist_ok=True)
    _diag_logging.basicConfig(filename=_diag_os.path.join(_diag_base, "startup.log"), level=_diag_logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8", force=True)
    _diag_logging.info("=== Order Manager startup ===")
    _diag_logging.info("Python=%s frozen=%s executable=%s", _diag_sys.version, bool(getattr(_diag_sys, "frozen", False)), _diag_sys.executable)
    def _diag_excepthook(exc_type, exc_value, exc_tb):
        _diag_logging.critical("Unhandled startup/runtime exception", exc_info=(exc_type, exc_value, exc_tb))
        _diag_sys.__excepthook__(exc_type, exc_value, exc_tb)
    _diag_sys.excepthook = _diag_excepthook
except Exception:
    pass

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
from PySide6.QtGui import QBrush, QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QDialog, QFormLayout, QSpinBox, QDateEdit, QComboBox, QFileDialog, QCheckBox, QFrame, QHeaderView
)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from version import APP_VERSION

def due_date_highlight_color(due_date, status, today=None):
    """Return a visible row color based on the due date; completed orders are never highlighted."""
    if str(status or "").strip() == "완료":
        return None
    due_text = str(due_date or "").strip()
    if not due_text:
        return None
    try:
        due = datetime.strptime(due_text[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    days = (due - (today or date.today())).days
    if days <= 1:
        return "#ffc2c2"
    if days <= 3:
        return "#ffdda8"
    if days <= 7:
        return "#fff0a6"
    return None

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
        temp = BACKUP_DIR / f"orders_{stamp}.tmp.sqlite3"
        source = None
        destination = None
        try:
            # sqlite3.Connection's `with` statement commits/rolls back but does
            # NOT close the handle. Explicit close is required on Windows before rename.
            source = sqlite3.connect(DB_PATH, timeout=30)
            destination = sqlite3.connect(temp, timeout=30)
            source.backup(destination)
            destination.commit()
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()
        try:
            # Both SQLite handles are now closed, so Windows can rename the file.
            temp.replace(target)
            cutoff = datetime.now() - timedelta(days=30)
            for old in BACKUP_DIR.glob("orders_*.sqlite3"):
                try:
                    if old != target and datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
                        old.unlink()
                except OSError:
                    pass
            return target
        except Exception:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

def init_db():
    with db_connect() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_no TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            vendor TEXT NOT NULL,
            order_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '진행중',
            updated_at TEXT NOT NULL
        )""")
        con.commit()

def create_api():
    api = Flask(__name__)
    init_db()

    @api.get("/api/health")
    def health():
        return jsonify({"ok": True, "app": APP_NAME})

    @api.get("/api/orders")
    def get_orders():
        with db_connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM orders ORDER BY due_date ASC, id DESC").fetchall()
            return jsonify([dict(r) for r in rows])

    @api.post("/api/orders")
    def add_order():
        d = request.get_json(force=True)
        # All order fields are optional. Validate only values the user supplied.
        qty_text = str(d.get("quantity", "")).strip()
        due_date = str(d.get("due_date", "")).strip()
        order_date = str(d.get("order_date", "")).strip()
        try:
            qty = int(qty_text) if qty_text else ""
            if qty_text and qty <= 0: raise ValueError()
            if due_date: datetime.strptime(due_date, "%Y-%m-%d")
            if order_date: datetime.strptime(order_date, "%Y-%m-%d")
        except Exception:
            return jsonify({"error": "수량은 양의 정수, 날짜는 YYYY-MM-DD 형식으로 입력해주세요."}), 400
        with db_connect() as con:
            cur = con.execute("""INSERT INTO orders
            (item_no,item_name,quantity,due_date,vendor,order_date,status,updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (str(d.get("item_no", "")).strip(), str(d.get("item_name", "")).strip(), qty, due_date,
             str(d.get("vendor", "")).strip(), order_date, d.get("status", "진행중"),
             datetime.now().isoformat(timespec="seconds")))
            con.commit()
            return jsonify({"id": cur.lastrowid})

    @api.put("/api/orders/<int:order_id>")
    def update_order(order_id):
        d = request.get_json(force=True)
        # All order fields are optional. Validate only values the user supplied.
        qty_text = str(d.get("quantity", "")).strip()
        due_date = str(d.get("due_date", "")).strip()
        order_date = str(d.get("order_date", "")).strip()
        try:
            qty = int(qty_text) if qty_text else ""
            if qty_text and qty <= 0: raise ValueError()
            if due_date: datetime.strptime(due_date, "%Y-%m-%d")
            if order_date: datetime.strptime(order_date, "%Y-%m-%d")
        except Exception:
            return jsonify({"error": "수량은 양의 정수, 날짜는 YYYY-MM-DD 형식으로 입력해주세요."}), 400
        with db_connect() as con:
            cur = con.execute("""UPDATE orders SET item_no=?,item_name=?,quantity=?,due_date=?,
            vendor=?,order_date=?,status=?,updated_at=? WHERE id=?""",
            (str(d.get("item_no", "")).strip(), str(d.get("item_name", "")).strip(), qty, due_date,
             str(d.get("vendor", "")).strip(), order_date, d.get("status", "진행중"),
             datetime.now().isoformat(timespec="seconds"), order_id))
            con.commit()
            if cur.rowcount == 0: return jsonify({"error": "해당 발주를 찾을 수 없습니다."}), 404
        return jsonify({"ok": True})

    @api.post("/api/orders/<int:order_id>/deliver")
    def deliver_order(order_id):
        d = request.get_json(force=True) or {}
        raw = str(d.get("quantity", "")).replace(",", "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            return jsonify({"error": "납품 수량은 0보다 큰 양의 정수로 입력해주세요."}), 400
        delivered_qty = int(raw)
        try:
            with db_connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT quantity FROM orders WHERE id=?", (order_id,)).fetchone()
                if row is None:
                    con.rollback()
                    return jsonify({"error": "해당 발주를 찾을 수 없습니다."}), 404
                try:
                    current_qty = int(row[0])
                except (TypeError, ValueError):
                    con.rollback()
                    return jsonify({"error": "기존 발주 수량이 숫자가 아니므로 납품 처리할 수 없습니다."}), 400
                if current_qty <= 0:
                    con.rollback()
                    return jsonify({"error": "기존 발주 수량이 0 이하이므로 납품 처리할 수 없습니다."}), 400
                if delivered_qty >= current_qty:
                    con.execute("DELETE FROM orders WHERE id=?", (order_id,))
                    result, remaining = "deleted", 0
                else:
                    remaining = current_qty - delivered_qty
                    con.execute("UPDATE orders SET quantity=?, updated_at=? WHERE id=?",
                                (remaining, datetime.now().isoformat(timespec="seconds"), order_id))
                    result = "updated"
                con.commit()
            return jsonify({"ok": True, "result": result, "remaining": remaining})
        except sqlite3.Error as exc:
            return jsonify({"error": f"데이터베이스 처리 실패: {exc}"}), 500

    @api.delete("/api/orders/<int:order_id>")
    def delete_order(order_id):
        with db_connect() as con:
            cur = con.execute("DELETE FROM orders WHERE id=?", (order_id,))
            con.commit()
            if cur.rowcount == 0: return jsonify({"error": "해당 발주를 찾을 수 없습니다."}), 404
        return jsonify({"ok": True})
    return api

class ApiServer:
    def __init__(self):
        self.server = None
        self.thread = None
    def start(self):
        if self.server: return
        init_db()
        backup_database()
        self.server = make_server("0.0.0.0", PORT, create_api(), threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server = None

class OrderDialog(QDialog):
    def __init__(self, parent=None, row=None):
        super().__init__(parent)
        self.setWindowTitle("발주 등록 / 수정")
        self.setMinimumWidth(390)
        form = QFormLayout(self)
        self.item_no = QLineEdit()
        self.item_name = QLineEdit()
        self.quantity = QLineEdit(); self.quantity.setPlaceholderText("선택 입력")
        self.due_date = QDateEdit()
        self.order_date = QDateEdit()
        for date_field in (self.due_date, self.order_date):
            date_field.setCalendarPopup(True)
            date_field.setDisplayFormat("yyyy-MM-dd")
            date_field.setMinimumDate(QDate(1900, 1, 1))
            date_field.setSpecialValueText("선택 안 함")
            # 새 발주 등록 시 날짜 기본값은 오늘로 설정
            date_field.setDate(QDate.currentDate())
        self.vendor = QLineEdit()
        self.status = QComboBox(); self.status.addItems(["진행중", "완료"])
        form.addRow("고객사", self.vendor)
        form.addRow("품번", self.item_no)
        form.addRow("품명", self.item_name)
        form.addRow("수량", self.quantity)
        form.addRow("발주일", self.order_date)
        form.addRow("납기일", self.due_date)
        form.addRow("상태", self.status)
        buttons = QHBoxLayout()
        ok = QPushButton("저장"); cancel = QPushButton("취소")
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        buttons.addWidget(ok); buttons.addWidget(cancel)
        form.addRow(buttons)
        if row:
            self.item_no.setText(str(row.get("item_no", "")))
            self.item_name.setText(str(row.get("item_name", "")))
            self.quantity.setText(str(row.get("quantity", "")))
            self.due_date.setDate(QDate.fromString(row["due_date"], "yyyy-MM-dd") if row.get("due_date") else self.due_date.minimumDate())
            self.vendor.setText(str(row.get("vendor", "")))
            self.order_date.setDate(QDate.fromString(row["order_date"], "yyyy-MM-dd") if row.get("order_date") else self.order_date.minimumDate())
            self.status.setCurrentText(row.get("status", "진행중"))
    def data(self):
        return {
            "item_no": self.item_no.text().strip(),
            "item_name": self.item_name.text().strip(),
            "quantity": self.quantity.text().strip(),
            "due_date": "" if self.due_date.date() == self.due_date.minimumDate() else self.due_date.date().toString("yyyy-MM-dd"),
            "vendor": self.vendor.text().strip(),
            "order_date": "" if self.order_date.date() == self.order_date.minimumDate() else self.order_date.date().toString("yyyy-MM-dd"),
            "status": self.status.currentText()
        }

class DeliveryDialog(QDialog):
    def __init__(self, current_quantity, parent=None):
        super().__init__(parent)
        self.setWindowTitle("납품 수량 입력")
        self.setMinimumWidth(340)
        self.current_quantity = int(current_quantity)
        form = QFormLayout(self)
        form.addRow("현재 발주 수량", QLabel(f"{self.current_quantity:,}개"))
        self.quantity = QLineEdit()
        self.quantity.setPlaceholderText("실제 납품한 수량 입력")
        self.quantity.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9,]*"), self.quantity))
        form.addRow("실제 납품 수량", self.quantity)
        hint = QLabel("현재 발주 수량보다 크게 입력하면 전량 납품 완료 처리됩니다.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7A8495;")
        form.addRow(hint)
        buttons = QHBoxLayout()
        ok, cancel = QPushButton("납품 처리"), QPushButton("취소")
        ok.clicked.connect(self._accept_if_valid)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        form.addRow(buttons)
        self.quantity.editingFinished.connect(self._format_quantity)

    def _format_quantity(self):
        raw = self.quantity.text().replace(",", "").strip()
        if raw.isdigit() and raw:
            self.quantity.setText(f"{int(raw):,}")

    def _accept_if_valid(self):
        raw = self.quantity.text().replace(",", "").strip()
        if not raw or not raw.isdigit() or int(raw) <= 0:
            QMessageBox.warning(self, "입력 확인", "납품 수량은 0보다 큰 양의 정수로 입력해주세요.")
            return
        self.accept()

    def value(self):
        return int(self.quantity.text().replace(",", "").strip())


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)

class RequestWorker(QRunnable):
    """Run HTTP requests outside the GUI thread."""
    def __init__(self, method, url, kwargs=None):
        super().__init__()
        self.method, self.url, self.kwargs = method, url, kwargs or {}
        self.signals = WorkerSignals()

    def run(self):
        try:
            response = requests.request(self.method, self.url, timeout=(2, 3), **self.kwargs)
            response.raise_for_status()
            self.signals.finished.emit(response.json() if response.content else {})
        except Exception as exc:
            self.signals.failed.emit(str(exc))

class UpdateWorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class UpdateCheckWorker(QRunnable):
    def __init__(self, operation, release=None):
        super().__init__()
        self.operation = operation
        self.release = release
        self.signals = UpdateWorkerSignals()

    def run(self):
        try:
            if self.operation == "check":
                cfg = load_update_config()
                repo = str(cfg.get("github_repo", "")).strip()
                if not repo or repo == "OWNER/REPOSITORY" or "/" not in repo:
                    raise RuntimeError("update_config.json에 GitHub 저장소(OWNER/REPOSITORY)를 설정해주세요.")
                api_url = cfg.get("release_api", "https://api.github.com/repos/{github_repo}/releases/latest").format(github_repo=repo)
                response = requests.get(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "OrderManager-Updater"}, timeout=12)
                response.raise_for_status()
                release = response.json()
                assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
                package_url = assets.get("OrderManager_Update.zip")
                hash_url = assets.get("OrderManager_Update.zip.sha256")
                if not package_url or not hash_url:
                    raise RuntimeError("최신 GitHub Release에 업데이트 ZIP 또는 SHA-256 파일이 없습니다.")
                remote_version = str(release.get("tag_name", "")).lstrip("vV")
                if not remote_version:
                    raise RuntimeError("Release 태그에서 버전을 확인할 수 없습니다.")
                self.signals.finished.emit({"version": remote_version, "package_url": package_url, "hash_url": hash_url, "html_url": release.get("html_url", "")})
            elif self.operation == "download":
                release = self.release
                UPDATE_DIR.mkdir(parents=True, exist_ok=True)
                package = UPDATE_DIR / "OrderManager_Update.zip"
                hash_path = UPDATE_DIR / "OrderManager_Update.zip.sha256"
                for url, path in ((release["package_url"], package), (release["hash_url"], hash_path)):
                    response = requests.get(url, timeout=60, stream=True, headers={"User-Agent": "OrderManager-Updater"})
                    response.raise_for_status()
                    with path.open("wb") as stream:
                        for chunk in response.iter_content(1024 * 1024):
                            if chunk:
                                stream.write(chunk)
                expected = hash_path.read_text(encoding="utf-8-sig").strip().split()[0].lower()
                digest = hashlib.sha256(package.read_bytes()).hexdigest()
                if len(expected) != 64 or digest != expected:
                    package.unlink(missing_ok=True)
                    raise RuntimeError("업데이트 파일 SHA-256 검증에 실패했습니다.")
                self.signals.finished.emit({"package": str(package), "sha256": expected})
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class MainWindow(QMainWindow):
    headers = ["고객사", "품번", "품명", "수량", "발주일", "납기일", "상태"]
    keys = ["vendor", "item_no", "item_name", "quantity", "order_date", "due_date", "status"]
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.api_server = ApiServer()
        if self.config["server_mode"]:
            try: self.api_server.start()
            except Exception as e: QMessageBox.critical(self, "서버 시작 실패", str(e))
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 700)
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(26, 22, 26, 16)
        root.setSpacing(15)

        # Header
        header_panel = QFrame()
        header_panel.setObjectName("headerPanel")
        header = QHBoxLayout(header_panel)
        header.setContentsMargins(22, 17, 22, 17)
        header.setSpacing(14)

        title_box = QVBoxLayout()
        title = QLabel("발주 관리")
        title.setObjectName("pageTitle")
        subtitle = QLabel("사내 발주 현황을 한곳에서 관리하세요")
        subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self.connection = QLabel("● 연결 확인 중")
        self.connection.setObjectName("connectionBadge")
        header.addWidget(self.connection)
        root.addWidget(header_panel)

        # Connection and startup settings
        settings_panel = QFrame()
        settings_panel.setObjectName("surfacePanel")
        settings = QHBoxLayout(settings_panel)
        settings.setContentsMargins(18, 12, 18, 12)
        settings.setSpacing(12)

        server_label = QLabel("서버 주소")
        server_label.setObjectName("fieldLabel")
        self.host = QLineEdit(self.config["server_host"])
        self.host.setPlaceholderText("메인 PC IP 주소")
        self.host.setFixedWidth(170)
        self.server_check = QCheckBox("이 PC를 메인 서버로 사용")
        self.server_check.setChecked(self.config["server_mode"])
        self.autostart_check = QCheckBox("Windows 로그인 시 자동 실행")
        self.autostart_check.setChecked(self.config.get("auto_start", False))
        save_conn = QPushButton("설정 저장")
        save_conn.setObjectName("secondaryButton")
        save_conn.clicked.connect(self.save_connection)

        settings.addWidget(server_label)
        settings.addWidget(self.host)
        settings.addSpacing(8)
        settings.addWidget(self.server_check)
        settings.addWidget(self.autostart_check)
        settings.addStretch()
        settings.addWidget(save_conn)
        root.addWidget(settings_panel)

        # Main action toolbar
        actions_panel = QFrame()
        actions_panel.setObjectName("surfacePanel")
        actions = QHBoxLayout(actions_panel)
        actions.setContentsMargins(14, 12, 14, 12)
        actions.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("⌕  고객사, 품번, 품명 등 전체 검색")
        self.search.setMinimumWidth(190)
        self.search.textChanged.connect(self.apply_filter)

        add = QPushButton("＋  발주 등록")
        add.setObjectName("primaryButton")
        add.clicked.connect(self.add_order)
        edit = QPushButton("수정")
        edit.setObjectName("secondaryButton")
        edit.clicked.connect(self.edit_order)
        delete = QPushButton("삭제")
        delete.setObjectName("dangerButton")
        delete.clicked.connect(self.delete_order)
        delivered = QPushButton("납품 완료")
        delivered.setObjectName("successButton")
        delivered.clicked.connect(self.mark_delivered)
        export = QPushButton("엑셀 내보내기")
        export.setObjectName("secondaryButton")
        export.clicked.connect(self.export_excel)
        refresh = QPushButton("새로고침")
        refresh.setObjectName("secondaryButton")
        refresh.clicked.connect(self.refresh)
        backup = QPushButton("백업")
        backup.setObjectName("secondaryButton")
        backup.clicked.connect(self.manual_backup)
        self.update_button = QPushButton("업데이트 확인")
        self.update_button.setObjectName("secondaryButton")
        self.update_button.clicked.connect(self.on_update_button_clicked)

        actions.addWidget(self.search, 1)
        actions.addWidget(add)
        actions.addWidget(edit)
        actions.addWidget(delete)
        actions.addWidget(delivered)
        actions.addWidget(export)
        actions.addWidget(refresh)
        actions.addWidget(backup)
        actions.addWidget(self.update_button)
        root.addWidget(actions_panel)

        # Compact filters
        filters_panel = QFrame()
        filters_panel.setObjectName("surfacePanel")
        filter_row = QHBoxLayout(filters_panel)
        filter_row.setContentsMargins(18, 10, 18, 10)
        filter_row.setSpacing(10)
        filter_title = QLabel("상세 필터")
        filter_title.setObjectName("fieldLabel")
        filter_row.addWidget(filter_title)

        self.filters = {}
        for key, label in [("vendor", "고객사"), ("item_no", "품번"), ("item_name", "품명"), ("status", "상태")]:
            field = QLineEdit()
            field.setPlaceholderText(f"{label} 필터")
            field.setMaximumWidth(170)
            field.textChanged.connect(self.apply_filter)
            self.filters[key] = field
            filter_row.addWidget(field)
        filter_row.addStretch()
        root.addWidget(filters_panel)

        # Order list
        table_panel = QFrame()
        table_panel.setObjectName("tablePanel")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(10, 10, 10, 10)
        table_layout.setSpacing(0)

        self.table = QTableWidget(0, len(self.headers))
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setAlternatingRowColors(False)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.SolidLine)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setMinimumHeight(42)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table.doubleClicked.connect(self.edit_order)
        table_layout.addWidget(self.table)
        root.addWidget(table_panel, 1)

        self.statusbar = self.statusBar()
        self.thread_pool = QThreadPool.globalInstance()
        self._polling = False
        self._last_data_signature = None
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(5000)
        self.backup_timer = QTimer(self); self.backup_timer.timeout.connect(self.maybe_daily_backup); self.backup_timer.start(60000)
        self.last_daily_backup = None
        self.rows = []
        self.refresh()
        QTimer.singleShot(2500, self.check_updates)

    def on_update_button_clicked(self):
        release = getattr(self, "_available_release", None)
        if release and version_tuple(release.get("version", "0")) > version_tuple(APP_VERSION):
            self.download_update()
        else:
            self.check_updates()

    def check_updates(self):
        if getattr(self, "_update_check_running", False):
            self.statusbar.showMessage("업데이트를 확인하고 있습니다...", 5000)
            return
        self._update_check_running = True
        self.update_button.setEnabled(False)
        self.update_button.setText("확인 중...")
        worker = UpdateCheckWorker("check")
        worker.signals.finished.connect(self._on_update_check)
        worker.signals.failed.connect(self._on_update_error)
        self.thread_pool.start(worker)

    def _on_update_check(self, release):
        self._update_check_running = False
        self.update_button.setEnabled(True)
        self.update_button.setText("업데이트 확인")
        self._available_release = release
        if version_tuple(release["version"]) > version_tuple(APP_VERSION):
            self.update_button.setText(f"업데이트 v{release['version']}")
            self.statusbar.showMessage(f"새 버전 v{release['version']}을 사용할 수 있습니다. 업데이트 버튼을 눌러주세요.", 20000)
            answer = QMessageBox.question(self, "업데이트 발견", f"새 버전 v{release['version']}이 있습니다.\n현재 버전: v{APP_VERSION}\n\n지금 업데이트할까요?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer == QMessageBox.Yes:
                self.download_update()
        else:
            message = f"현재 최신 버전입니다.\n설치된 버전: v{APP_VERSION}"
            self.statusbar.showMessage(f"최신 버전입니다. (현재 v{APP_VERSION})", 10000)
            QMessageBox.information(self, "업데이트 확인", message)

    def _on_update_error(self, error):
        self._update_check_running = False
        self.update_button.setEnabled(True)
        self.update_button.setText("업데이트 확인")
        self.statusbar.showMessage(f"업데이트 확인 실패: {error}", 15000)
        QMessageBox.warning(self, "업데이트 확인 실패", f"GitHub에서 업데이트 정보를 가져오지 못했습니다.\n\n{error}")

    def download_update(self):
        if not getattr(self, "_available_release", None):
            self.check_updates()
            return
        if self.config.get("server_mode"):
            answer = QMessageBox.warning(self, "메인 서버 업데이트", "이 PC는 메인 서버입니다.\n직원 PC보다 먼저 업데이트해야 합니다. 업데이트 중에는 직원 PC의 발주 기능을 사용할 수 없습니다.\n\n계속할까요?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        else:
            answer = QMessageBox.question(self, "업데이트", "업데이트 파일을 다운로드하고 프로그램을 다시 시작합니다.\n진행할까요?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer != QMessageBox.Yes:
                return
        self.update_button.setEnabled(False)
        self.update_button.setText("다운로드 중...")
        worker = UpdateCheckWorker("download", self._available_release)
        worker.signals.finished.connect(self._on_update_downloaded)
        worker.signals.failed.connect(self._on_update_error)
        self.thread_pool.start(worker)

    def _on_update_downloaded(self, result):
        updater = BASE_DIR / "Updater.exe"
        install_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        if not updater.is_file():
            self.update_button.setEnabled(True)
            self.update_button.setText("업데이트 확인")
            QMessageBox.critical(self, "Updater 없음", f"업데이트 도구를 찾을 수 없습니다.\n{updater}\n\n설치 파일로 먼저 설치하거나 Updater.exe를 해당 경로에 배치해주세요.")
            return
        if os.name != "nt":
            QMessageBox.warning(self, "지원 환경", "자동 업데이트는 Windows 설치본에서만 실행할 수 있습니다.")
            return
        args = [str(updater), "--install-dir", str(install_dir), "--package", result["package"], "--sha256", result["sha256"], "--pid", str(os.getpid())]
        params = subprocess.list2cmdline(args[1:])
        try:
            code = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(updater), params, str(BASE_DIR), 1)
            if code <= 32:
                raise OSError(f"Updater 실행 실패 (코드 {code})")
            self.close()
            QApplication.quit()
        except Exception as exc:
            QMessageBox.critical(self, "업데이트 실행 실패", str(exc))
            self.update_button.setEnabled(True)
            self.update_button.setText("업데이트 확인")

    def manual_backup(self):
        if not self.config.get("server_mode"):
            QMessageBox.information(self, "백업 안내", "백업은 메인 서버 PC에서 실행할 수 있습니다.")
            return
        try:
            path = backup_database()
            QMessageBox.information(self, "백업 완료", f"발주 데이터 백업을 완료했습니다.\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "백업 실패", str(e))

    def maybe_daily_backup(self):
        if not self.config.get("server_mode"):
            return
        now = datetime.now()
        if now.hour >= 18 and self.last_daily_backup != date.today():
            try:
                backup_database()
                self.last_daily_backup = date.today()
                self.statusbar.showMessage("오늘 발주 데이터 자동 백업 완료", 10000)
            except Exception as e:
                self.statusbar.showMessage(f"자동 백업 실패: {e}", 15000)

    def base_url(self):
        host = "127.0.0.1" if self.config["server_mode"] else self.config["server_host"].strip()
        return f"http://{host}:{PORT}"

    def save_connection(self):
        self.config["server_mode"] = self.server_check.isChecked()
        self.config["server_host"] = self.host.text().strip() or "127.0.0.1"
        self.config["auto_start"] = self.autostart_check.isChecked()
        save_config(self.config)
        try:
            set_windows_autostart(self.config["auto_start"])
        except Exception as e:
            QMessageBox.warning(self, "자동 실행 설정", f"자동 실행 등록에 실패했습니다.\n{e}")
        if self.config["server_mode"]:
            try:
                self.api_server.start()
                self.statusbar.showMessage("메인 서버 실행 중 - 포트 8765", 5000)
            except Exception as e:
                QMessageBox.critical(self, "서버 오류", f"서버를 시작하지 못했습니다.\n{e}")
        else:
            self.api_server.stop()
        self.refresh()

    def request(self, method, path, **kwargs):
        # Kept for short user-initiated operations; background polling uses RequestWorker.
        return requests.request(method, self.base_url()+path, timeout=(2, 3), **kwargs)

    def refresh(self):
        # Never perform network I/O on the GUI thread. Also prevent overlapping polls.
        if self._polling:
            return
        self._polling = True
        worker = RequestWorker("GET", self.base_url()+"/api/orders")
        worker.signals.finished.connect(self._on_refresh_success)
        worker.signals.failed.connect(self._on_refresh_failed)
        self.thread_pool.start(worker)

    def _on_refresh_success(self, rows):
        self._polling = False
        signature = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        self.connection.setText("● 서버 연결됨")
        self.connection.setProperty("state", "connected")
        self.connection.style().unpolish(self.connection)
        self.connection.style().polish(self.connection)
        if signature != self._last_data_signature:
            self._last_data_signature = signature
            self.rows = rows
            self.render()
        self.statusbar.showMessage(f"총 {len(rows)}건 | 5초마다 자동 동기화")

    def _on_refresh_failed(self, error):
        self._polling = False
        self.connection.setText("● 서버 연결 안 됨")
        self.connection.setProperty("state", "disconnected")
        self.connection.style().unpolish(self.connection)
        self.connection.style().polish(self.connection)
        self.statusbar.showMessage(f"서버 연결 실패: {error} ({self.base_url()})")

    def render(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for row in self.rows:
            i = self.table.rowCount(); self.table.insertRow(i)
            raw_quantity = row.get("quantity", "")
            try:
                quantity_text = f"{int(raw_quantity):,}" if str(raw_quantity).strip() else ""
            except (TypeError, ValueError):
                quantity_text = str(raw_quantity)
            values = [row.get("vendor", ""), row.get("item_no", ""), row.get("item_name", ""), quantity_text,
                      row.get("order_date", ""), row.get("due_date", ""), row.get("status","진행중")]
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row["id"])
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i,c,item)
            color = due_date_highlight_color(row.get("due_date"), row.get("status"))
            if color:
                brush = QBrush(QColor(color))
                for c in range(len(self.headers)):
                    cell = self.table.item(i, c)
                    if cell is not None:
                        cell.setBackground(brush)
                        cell.setForeground(QBrush(QColor("#4a2525")))
        self.table.setSortingEnabled(True)
        self.table.viewport().update()
        self.apply_filter()

    def apply_filter(self):
        text = self.search.text().strip().lower() if hasattr(self, "search") else ""
        filters = {k:v.text().strip().lower() for k,v in self.filters.items()} if hasattr(self,"filters") else {}
        for i in range(self.table.rowCount()):
            values = {"vendor": self.table.item(i,0).text().lower(),
                      "item_no": self.table.item(i,1).text().lower(),
                      "item_name": self.table.item(i,2).text().lower(),
                      "status": self.table.item(i,6).text().lower()}
            visible = not text or any(text in self.table.item(i,c).text().lower() for c in range(self.table.columnCount()))
            for k, val in filters.items():
                if val and val not in values.get(k,""): visible = False
            self.table.setRowHidden(i, not visible)

    def selected_id(self):
        items = self.table.selectedItems()
        return int(items[0].data(Qt.UserRole)) if items else None

    def add_order(self):
        dlg = OrderDialog(self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.data()
            try:
                r = self.request("POST","/api/orders",json=data)
                if r.status_code >= 400: raise Exception(r.json().get("error","등록 실패"))
                self.refresh()
            except Exception as e: QMessageBox.critical(self,"등록 실패",str(e))

    def edit_order(self):
        oid = self.selected_id()
        if not oid: QMessageBox.information(self,"선택 필요","수정할 발주를 선택해주세요."); return
        row = next((x for x in self.rows if x["id"] == oid),None)
        if not row: return
        dlg = OrderDialog(self,row)
        if dlg.exec() == QDialog.Accepted:
            try:
                r = self.request("PUT",f"/api/orders/{oid}",json=dlg.data())
                if r.status_code >= 400: raise Exception(r.json().get("error","수정 실패"))
                self.refresh()
            except Exception as e: QMessageBox.critical(self,"수정 실패",str(e))

    def mark_delivered(self):
        oid = self.selected_id()
        if not oid:
            QMessageBox.information(self, "선택 필요", "납품 처리할 발주를 선택해주세요.")
            return
        row = next((r for r in self.rows if r.get("id") == oid), None)
        if not row:
            QMessageBox.warning(self, "발주 확인", "선택한 발주 정보를 찾을 수 없습니다. 새로고침 후 다시 시도해주세요.")
            return
        try:
            current_qty = int(row.get("quantity"))
        except (TypeError, ValueError):
            QMessageBox.warning(self, "수량 확인", "현재 발주 수량이 숫자가 아니므로 납품 처리할 수 없습니다.")
            return
        if current_qty <= 0:
            QMessageBox.warning(self, "수량 확인", "현재 발주 수량이 0 이하이므로 납품 처리할 수 없습니다.")
            return
        dialog = DeliveryDialog(current_qty, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            response = self.request("POST", f"/api/orders/{oid}/deliver", json={"quantity": dialog.value()})
            if response.status_code >= 400:
                raise Exception(response.json().get("error", "납품 처리 실패"))
            result = response.json()
            if result.get("result") == "deleted":
                QMessageBox.information(self, "납품 완료", "전량 납품 처리했습니다. 발주 내역에서 삭제되었습니다.")
            else:
                QMessageBox.information(self, "부분 납품 완료",
                                        f"납품 처리했습니다. 남은 발주 수량: {result.get('remaining', 0):,}개")
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "납품 처리 실패", str(e))

    def delete_order(self):
        oid = self.selected_id()
        if not oid: QMessageBox.information(self,"선택 필요","삭제할 발주를 선택해주세요."); return
        if QMessageBox.question(self,"삭제 확인","선택한 발주를 삭제할까요?") != QMessageBox.Yes: return
        try:
            r = self.request("DELETE",f"/api/orders/{oid}")
            if r.status_code >= 400: raise Exception(r.json().get("error","삭제 실패"))
            self.refresh()
        except Exception as e: QMessageBox.critical(self,"삭제 실패",str(e))

    def export_excel(self):
        visible = []
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                visible.append([self.table.item(i,c).text() for c in range(self.table.columnCount())])
        if not visible:
            QMessageBox.information(self,"엑셀 내보내기","내보낼 발주 내역이 없습니다."); return
        path,_ = QFileDialog.getSaveFileName(self,"엑셀 저장",f"발주내역_{date.today():%Y%m%d}.xlsx","Excel 파일 (*.xlsx)")
        if not path: return
        wb = Workbook(); ws = wb.active; ws.title = "발주 내역"
        ws.append(self.headers)
        for row in visible:
            excel_row = list(row)
            try:
                excel_row[3] = int(str(excel_row[3]).replace(",", "").strip())
            except (ValueError, TypeError):
                pass
            ws.append(excel_row)
            if isinstance(excel_row[3], int):
                ws.cell(row=ws.max_row, column=4).number_format = '#,##0'
        for cell in ws[1]:
            cell.font = Font(bold=True,color="FFFFFF")
            cell.fill = PatternFill("solid",fgColor="355C7D")
            cell.alignment = Alignment(horizontal="center")
        for excel_row in ws.iter_rows(min_row=2):
            for cell in excel_row:
                cell.alignment = Alignment(horizontal="center", vertical="center")
        widths = [22,12,22,12,14,14,12]
        for idx,width in enumerate(widths,1): ws.column_dimensions[get_column_letter(idx)].width = width
        ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
        try:
            wb.save(path)
            QMessageBox.information(self,"완료",f"엑셀 파일을 저장했습니다.\n{path}")
        except Exception as e: QMessageBox.critical(self,"저장 실패",str(e))

    def closeEvent(self,event):
        if self.config.get("server_mode"):
            try:
                path = backup_database()
                self.statusbar.showMessage(f"종료 전 백업 완료: {path}")
            except Exception as e:
                answer = QMessageBox.question(
                    self, "백업 실패", f"종료 전 백업에 실패했습니다.\n{e}\n그래도 종료할까요?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if answer != QMessageBox.Yes:
                    event.ignore()
                    return
        self.api_server.stop()
        event.accept()

from PySide6.QtGui import QBrush, QColor
def PatternBrush(color):
    return QBrush(QColor(color))

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget {
            font-family: "Segoe UI", "Malgun Gothic";
            font-size: 10pt;
            color: #202938;
        }
        QMainWindow, QWidget#qt_scrollarea_viewport {
            background: #F4F6FA;
        }
        QFrame#headerPanel, QFrame#surfacePanel, QFrame#tablePanel {
            background: #FFFFFF;
            border: 1px solid #E6EAF0;
            border-radius: 12px;
        }
        QLabel#pageTitle {
            color: #172033;
            font-size: 23pt;
            font-weight: 750;
        }
        QLabel#pageSubtitle {
            color: #7A8495;
            font-size: 9pt;
        }
        QLabel#fieldLabel {
            color: #586477;
            font-weight: 650;
        }
        QLabel#connectionBadge {
            background: #FFF4E5;
            color: #A15C00;
            border: 1px solid #F6D7A8;
            border-radius: 12px;
            padding: 7px 12px;
            font-weight: 650;
        }
        QLabel#connectionBadge[state="connected"] {
            background: #EAF8F0;
            color: #167647;
            border-color: #C8EBD7;
        }
        QLabel#connectionBadge[state="disconnected"] {
            background: #FFF0F0;
            color: #B42318;
            border-color: #F5CCCC;
        }
        QLineEdit, QDateEdit, QComboBox, QSpinBox {
            background: #FFFFFF;
            border: 1px solid #D9E0E9;
            border-radius: 7px;
            padding: 8px 10px;
            min-height: 20px;
            selection-background-color: #315EFB;
        }
        QLineEdit:focus, QDateEdit:focus, QComboBox:focus, QSpinBox:focus {
            border: 1px solid #5578F5;
        }
        QCheckBox {
            spacing: 7px;
            color: #485467;
        }
        QPushButton {
            background: #FFFFFF;
            color: #344054;
            border: 1px solid #D9E0E9;
            border-radius: 7px;
            padding: 8px 12px;
            min-height: 22px;
            font-weight: 600;
        }
        QPushButton:hover {
            background: #F2F5FA;
            border-color: #BFC9D8;
        }
        QPushButton:pressed {
            background: #E8EDF5;
        }
        QPushButton#primaryButton {
            background: #315EFB;
            color: #FFFFFF;
            border: 1px solid #315EFB;
        }
        QPushButton#primaryButton:hover {
            background: #244DE0;
        }
        QPushButton#dangerButton {
            color: #B42318;
            background: #FFF8F7;
            border-color: #F1D0CC;
        }
        QPushButton#dangerButton:hover {
            background: #FDECEB;
        }
        QPushButton#successButton {
            color: #167647;
            background: #EFFAF3;
            border-color: #C8EBD7;
        }
        QPushButton#successButton:hover {
            background: #E1F5E9;
        }
        QTableWidget {
            gridline-color: #E7ECF2;
            background: #FFFFFF;
            alternate-background-color: #F8FAFD;
            border: none;
            border-radius: 8px;
            color: #263244;
            selection-background-color: #E7EEFF;
            selection-color: #1E3A8A;
        }
        QHeaderView::section {
            background: #F3F6FA;
            color: #5B6678;
            border: none;
            border-right: 1px solid #E5EAF1;
            border-bottom: 1px solid #DCE3EC;
            padding: 10px 8px;
            font-weight: 700;
        }
        QTableWidget::item {
            padding: 7px;
            border: none;
        }
        QStatusBar {
            background: transparent;
            color: #7A8495;
            border: none;
        }
        QDialog {
            background: #F7F8FB;
        }
        QMessageBox {
            background: #FFFFFF;
        }
    """)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    try:
        _diag_logging.info("Calling main()")
        main()
    except BaseException:
        _diag_logging.exception("Fatal error while starting Order Manager")
        raise
