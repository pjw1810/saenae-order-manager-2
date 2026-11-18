import sys, os, json, sqlite3, threading, socket
from pathlib import Path
from datetime import date, datetime, timedelta
import shutil
from flask import Flask, request, jsonify
from werkzeug.serving import make_server
import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QDialog, QFormLayout, QSpinBox, QDateEdit, QComboBox, QFileDialog, QCheckBox
)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

APP_NAME = "사내 발주 관리"
PORT = 8765
BASE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SaenaeOrderManager"
BASE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "orders.sqlite3"
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

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
    """Create a consistent SQLite snapshot and retain the latest 30 daily files."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"orders_{date.today():%Y%m%d}.sqlite3"
    temp = target.with_suffix(".sqlite3.tmp")
    try:
        with sqlite3.connect(DB_PATH, timeout=15) as source:
            with sqlite3.connect(temp) as destination:
                source.backup(destination)
        temp.replace(target)
        cutoff = datetime.now() - timedelta(days=30)
        for old in BACKUP_DIR.glob("orders_*.sqlite3"):
            try:
                if datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
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
        required = ["item_no", "item_name", "quantity", "due_date", "vendor", "order_date"]
        if any(not str(d.get(k, "")).strip() for k in required):
            return jsonify({"error": "필수 항목을 모두 입력해주세요."}), 400
        try:
            qty = int(d["quantity"])
            if qty <= 0: raise ValueError()
            datetime.strptime(d["due_date"], "%Y-%m-%d")
            datetime.strptime(d["order_date"], "%Y-%m-%d")
        except Exception:
            return jsonify({"error": "수량 또는 날짜 형식이 올바르지 않습니다."}), 400
        with db_connect() as con:
            cur = con.execute("""INSERT INTO orders
            (item_no,item_name,quantity,due_date,vendor,order_date,status,updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (d["item_no"].strip(), d["item_name"].strip(), qty, d["due_date"],
             d["vendor"].strip(), d["order_date"], d.get("status", "진행중"),
             datetime.now().isoformat(timespec="seconds")))
            con.commit()
            return jsonify({"id": cur.lastrowid})

    @api.put("/api/orders/<int:order_id>")
    def update_order(order_id):
        d = request.get_json(force=True)
        required = ["item_no", "item_name", "quantity", "due_date", "vendor", "order_date"]
        if any(not str(d.get(k, "")).strip() for k in required):
            return jsonify({"error": "필수 항목을 모두 입력해주세요."}), 400
        try:
            qty = int(d["quantity"])
            if qty <= 0: raise ValueError()
            datetime.strptime(d["due_date"], "%Y-%m-%d")
            datetime.strptime(d["order_date"], "%Y-%m-%d")
        except Exception:
            return jsonify({"error": "수량 또는 날짜 형식이 올바르지 않습니다."}), 400
        with db_connect() as con:
            cur = con.execute("""UPDATE orders SET item_no=?,item_name=?,quantity=?,due_date=?,
            vendor=?,order_date=?,status=?,updated_at=? WHERE id=?""",
            (d["item_no"].strip(), d["item_name"].strip(), qty, d["due_date"],
             d["vendor"].strip(), d["order_date"], d.get("status", "진행중"),
             datetime.now().isoformat(timespec="seconds"), order_id))
            con.commit()
            if cur.rowcount == 0: return jsonify({"error": "해당 발주를 찾을 수 없습니다."}), 404
        return jsonify({"ok": True})

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
        self.quantity = QSpinBox(); self.quantity.setRange(1, 100000000); self.quantity.setValue(1)
        self.due_date = QDateEdit(); self.due_date.setCalendarPopup(True); self.due_date.setDisplayFormat("yyyy-MM-dd"); self.due_date.setDate(date.today())
        self.vendor = QLineEdit()
        self.order_date = QDateEdit(); self.order_date.setCalendarPopup(True); self.order_date.setDisplayFormat("yyyy-MM-dd"); self.order_date.setDate(date.today())
        self.status = QComboBox(); self.status.addItems(["진행중", "완료"])
        form.addRow("품번 *", self.item_no)
        form.addRow("품명 *", self.item_name)
        form.addRow("수량 *", self.quantity)
        form.addRow("납기일 *", self.due_date)
        form.addRow("거래처 *", self.vendor)
        form.addRow("발주일 *", self.order_date)
        form.addRow("상태", self.status)
        buttons = QHBoxLayout()
        ok = QPushButton("저장"); cancel = QPushButton("취소")
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        buttons.addWidget(ok); buttons.addWidget(cancel)
        form.addRow(buttons)
        if row:
            self.item_no.setText(row["item_no"]); self.item_name.setText(row["item_name"])
            self.quantity.setValue(int(row["quantity"]))
            self.due_date.setDate(datetime.strptime(row["due_date"], "%Y-%m-%d").date())
            self.vendor.setText(row["vendor"])
            self.order_date.setDate(datetime.strptime(row["order_date"], "%Y-%m-%d").date())
            self.status.setCurrentText(row.get("status", "진행중"))
    def data(self):
        return {
            "item_no": self.item_no.text().strip(),
            "item_name": self.item_name.text().strip(),
            "quantity": self.quantity.value(),
            "due_date": self.due_date.date().toString("yyyy-MM-dd"),
            "vendor": self.vendor.text().strip(),
            "order_date": self.order_date.date().toString("yyyy-MM-dd"),
            "status": self.status.currentText()
        }

class MainWindow(QMainWindow):
    headers = ["번호", "품번", "품명", "수량", "납기일", "거래처", "발주일", "상태"]
    keys = ["id", "item_no", "item_name", "quantity", "due_date", "vendor", "order_date", "status"]
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

        top = QHBoxLayout()
        title = QLabel("사내 발주 관리"); title.setStyleSheet("font-size:22px;font-weight:700;")
        self.connection = QLabel()
        self.host = QLineEdit(self.config["server_host"]); self.host.setPlaceholderText("메인 PC IP 주소")
        self.host.setMaximumWidth(180)
        self.server_check = QCheckBox("이 PC를 메인 서버로 사용")
        self.server_check.setChecked(self.config["server_mode"])
        self.autostart_check = QCheckBox("Windows 로그인 시 자동 실행")
        self.autostart_check.setChecked(self.config.get("auto_start", False))
        save_conn = QPushButton("설정 저장")
        save_conn.clicked.connect(self.save_connection)
        top.addWidget(title); top.addStretch(); top.addWidget(QLabel("서버 주소")); top.addWidget(self.host)
        top.addWidget(self.server_check); top.addWidget(self.autostart_check); top.addWidget(save_conn)
        root.addLayout(top)

        actions = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("전체 항목 검색")
        self.search.textChanged.connect(self.apply_filter)
        add = QPushButton("＋ 발주 등록"); add.clicked.connect(self.add_order)
        edit = QPushButton("선택 항목 수정"); edit.clicked.connect(self.edit_order)
        delete = QPushButton("선택 항목 삭제"); delete.clicked.connect(self.delete_order)
        export = QPushButton("엑셀 내보내기"); export.clicked.connect(self.export_excel)
        refresh = QPushButton("새로고침"); refresh.clicked.connect(self.refresh)
        backup = QPushButton("백업하기"); backup.clicked.connect(self.manual_backup)
        for w in [self.search, add, edit, delete, export, refresh, backup]: actions.addWidget(w)
        root.addLayout(actions)

        self.filters = {}
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("항목별 필터"))
        for key, label in [("item_no","품번"),("item_name","품명"),("vendor","거래처"),("status","상태")]:
            field = QLineEdit(); field.setPlaceholderText(label)
            field.setMaximumWidth(150); field.textChanged.connect(self.apply_filter)
            self.filters[key] = field; filter_row.addWidget(field)
        root.addLayout(filter_row)

        self.table = QTableWidget(0, len(self.headers))
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.edit_order)
        root.addWidget(self.table)

        self.statusbar = self.statusBar()
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(2500)
        self.backup_timer = QTimer(self); self.backup_timer.timeout.connect(self.maybe_daily_backup); self.backup_timer.start(60000)
        self.last_daily_backup = None
        self.rows = []
        self.refresh()

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
        return requests.request(method, self.base_url()+path, timeout=4, **kwargs)

    def refresh(self):
        try:
            r = self.request("GET", "/api/orders")
            r.raise_for_status()
            self.rows = r.json()
            self.connection.setText("● 연결됨")
            self.connection.setStyleSheet("color:#16803c;font-weight:600")
            self.render()
            self.statusbar.showMessage(f"총 {len(self.rows)}건 | 2.5초마다 자동 동기화")
        except Exception:
            self.connection.setText("● 서버 연결 안 됨")
            self.connection.setStyleSheet("color:#c0392b;font-weight:600")
            self.statusbar.showMessage(f"서버에 연결할 수 없습니다. 메인 PC 서버 실행 및 IP/방화벽을 확인하세요. ({self.base_url()})")

    def render(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for row in self.rows:
            i = self.table.rowCount(); self.table.insertRow(i)
            values = [row["id"], row["item_no"], row["item_name"], row["quantity"],
                      row["due_date"], row["vendor"], row["order_date"], row.get("status","진행중")]
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row["id"])
                if c in (0,3): item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i,c,item)
            if row.get("status") != "완료":
                try:
                    days = (datetime.strptime(row["due_date"], "%Y-%m-%d").date() - date.today()).days
                    color = None
                    if days <= 1: color = "#ffd6d6"
                    elif days <= 3: color = "#ffe7c2"
                    if color:
                        for c in range(len(self.headers)):
                            self.table.item(i,c).setBackground(PatternBrush(color))
                except Exception: pass
        self.table.setSortingEnabled(True)
        self.apply_filter()

    def apply_filter(self):
        text = self.search.text().strip().lower() if hasattr(self, "search") else ""
        filters = {k:v.text().strip().lower() for k,v in self.filters.items()} if hasattr(self,"filters") else {}
        for i in range(self.table.rowCount()):
            values = {k:self.table.item(i, idx+1).text().lower() for idx,k in enumerate(["item_no","item_name"])}
            values["vendor"] = self.table.item(i,5).text().lower()
            values["status"] = self.table.item(i,7).text().lower()
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
            if not all(str(data[k]).strip() for k in ["item_no","item_name","vendor"]):
                QMessageBox.warning(self,"입력 확인","품번, 품명, 거래처를 입력해주세요."); return
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
        for row in visible: ws.append(row)
        for cell in ws[1]:
            cell.font = Font(bold=True,color="FFFFFF")
            cell.fill = PatternFill("solid",fgColor="355C7D")
            cell.alignment = Alignment(horizontal="center")
        widths = [10,18,24,12,16,22,16,12]
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
    w = MainWindow(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
