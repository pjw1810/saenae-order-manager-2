"""Transactional Windows updater with SHA-256 validation and rollback."""
from __future__ import annotations
import argparse, hashlib, logging, os, shutil, subprocess, sys, time, zipfile
from pathlib import Path, PurePosixPath

def configure_logging() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SaenaeOrderManager"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "updater.log"
    logging.basicConfig(filename=path, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8", force=True)
    return path

def wait_for_process(pid: int, timeout: int = 120) -> None:
    """Wait for the GUI process to exit without sending it a signal.

    On Windows, os.kill(pid, 0) is NOT a safe existence check: values other
    than CTRL_C_EVENT/CTRL_BREAK_EVENT can terminate the target process.
    Use the Win32 process synchronization API instead.
    """
    if pid <= 0:
        return
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        WAIT_OBJECT_0 = 0x00000000
        WAIT_TIMEOUT = 0x00000102
        WAIT_FAILED = 0xFFFFFFFF
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            # ERROR_INVALID_PARAMETER means the process no longer exists.
            if error == 87:
                return
            raise OSError(error, f"OpenProcess failed for PID {pid}")
        try:
            result = kernel32.WaitForSingleObject(handle, max(0, timeout) * 1000)
            if result == WAIT_OBJECT_0:
                return
            if result == WAIT_TIMEOUT:
                raise TimeoutError(f"프로그램 종료 대기 시간 초과 (PID {pid})")
            if result == WAIT_FAILED:
                raise ctypes.WinError(ctypes.get_last_error())
            raise RuntimeError(f"알 수 없는 프로세스 대기 결과: {result}")
        finally:
            kernel32.CloseHandle(handle)
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        except OSError:
            return
        time.sleep(0.4)
    raise TimeoutError(f"프로그램 종료 대기 시간 초과 (PID {pid})")
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()

def safe_extract(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    with zipfile.ZipFile(archive, "r") as zf:
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            relative = PurePosixPath(name)
            if not name or relative.is_absolute() or ".." in relative.parts or ":" in name:
                raise ValueError(f"안전하지 않은 업데이트 파일 경로: {name}")
            mode = (member.external_attr >> 16) & 0xFFFF
            if mode & 0o170000 == 0o120000:
                raise ValueError(f"심볼릭 링크는 허용되지 않습니다: {name}")
            target = (destination / Path(*relative.parts)).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"staging 폴더 밖의 경로: {name}")
        zf.extractall(destination)
    payload = destination / "payload"
    if not payload.is_dir() or not (payload / "SaenaeOrderManager.exe").is_file():
        raise ValueError("업데이트 ZIP에 payload\\SaenaeOrderManager.exe가 없습니다.")
    return payload

def launch_and_check(exe: Path, install_dir: Path, seconds: int = 10):
    process = subprocess.Popen([str(exe)], cwd=str(install_dir))
    time.sleep(seconds)
    if process.poll() is not None:
        raise RuntimeError(f"새 버전이 시작 직후 종료되었습니다(exit={process.returncode}). startup.log를 확인하세요.")
    return process

def replace_installation(install_dir: Path, package: Path, expected_hash: str, pid: int, restart: bool = True) -> None:
    install_dir, package = install_dir.resolve(), package.resolve()
    if not install_dir.is_dir(): raise FileNotFoundError(f"설치 폴더 없음: {install_dir}")
    if not package.is_file(): raise FileNotFoundError(f"업데이트 ZIP 없음: {package}")
    expected_hash = expected_hash.strip().lower()
    if len(expected_hash) != 64 or sha256_file(package).lower() != expected_hash:
        raise ValueError("업데이트 ZIP SHA-256 검증 실패")
    wait_for_process(pid)
    parent = install_dir.parent
    suffix = time.strftime("%Y%m%d_%H%M%S") + "_" + str(os.getpid())
    staging = parent / (".OrderManager_staging_" + suffix)
    backup = parent / (".OrderManager_rollback_" + suffix)
    failed = parent / (".OrderManager_failed_" + suffix)
    payload = safe_extract(package, staging)
    moved_old = installed_new = False
    try:
        logging.info("Prepared update payload: %s", payload)
        install_dir.rename(backup)
        moved_old = True
        try:
            payload.rename(install_dir)
            installed_new = True
        except Exception:
            if backup.exists() and not install_dir.exists():
                backup.rename(install_dir)
                moved_old = False
            raise
        if restart:
            try:
                launch_and_check(install_dir / "SaenaeOrderManager.exe", install_dir)
            except Exception:
                logging.exception("New version failed startup check; rolling back")
                if installed_new and install_dir.exists():
                    install_dir.rename(failed)
                    installed_new = False
                if backup.exists():
                    backup.rename(install_dir)
                    moved_old = False
                try: subprocess.Popen([str(install_dir / "SaenaeOrderManager.exe")], cwd=str(install_dir))
                except Exception: logging.exception("Could not restart restored application")
                raise
        logging.info("Update completed. Rollback retained at %s", backup)
    except Exception:
        if moved_old and backup.exists() and not install_dir.exists():
            try: backup.rename(install_dir)
            except Exception: logging.exception("CRITICAL: rollback failed; backup remains at %s", backup)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

def main() -> int:
    log_path = configure_logging()
    parser = argparse.ArgumentParser(description="Order Manager Updater")
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()
    try:
        logging.info("Updater started: install_dir=%s package=%s pid=%s", args.install_dir, args.package, args.pid)
        replace_installation(Path(args.install_dir), Path(args.package), args.sha256, args.pid, not args.no_restart)
        return 0
    except Exception:
        logging.exception("Update failed; log=%s", log_path)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
