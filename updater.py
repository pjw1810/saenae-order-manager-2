"""Order Manager transactional updater. Standard-library only."""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath


def wait_for_process(pid: int, timeout: int = 120) -> None:
    if pid <= 0:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # os.kill(pid, 0) checks process existence on Windows and POSIX.
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.5)
    raise TimeoutError(f"프로그램 종료 대기 시간 초과 (PID {pid})")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive, "r") as zf:
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"안전하지 않은 업데이트 파일 경로: {name}")
            target = (destination / Path(*relative.parts)).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise ValueError(f"업데이트 경로가 설치 폴더를 벗어납니다: {name}")
        zf.extractall(destination)


def restore_backup(install_dir: Path, backup: Path) -> None:
    for child in install_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass
    for child in backup.iterdir():
        target = install_dir / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def replace_installation(install_dir: Path, package: Path, expected_hash: str, pid: int, restart: bool) -> None:
    install_dir = install_dir.resolve()
    if not install_dir.is_dir():
        raise FileNotFoundError(f"설치 폴더를 찾을 수 없습니다: {install_dir}")
    if sha256_file(package).lower() != expected_hash.strip().lower():
        raise ValueError("업데이트 파일 SHA-256 검증에 실패했습니다. 파일이 변경되었을 수 있습니다.")

    wait_for_process(pid)
    parent = install_dir.parent
    stamp = time.strftime("%Y%m%d_%H%M%S")
    staging = parent / f".OrderManager_staging_{stamp}"
    backup = parent / f".OrderManager_rollback_{stamp}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        safe_extract(package, staging)
        payload = staging / "payload"
        if not payload.is_dir():
            raise ValueError("업데이트 패키지에 payload 폴더가 없습니다.")
        exe = payload / "SaenaeOrderManager.exe"
        if not exe.is_file():
            raise ValueError("업데이트 패키지에 실행 파일이 없습니다.")

        shutil.copytree(install_dir, backup)
        try:
            # Remove only installed application files. User data is stored outside install_dir.
            for child in install_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            for child in payload.iterdir():
                target = install_dir / child.name
                if child.is_dir():
                    shutil.copytree(child, target)
                else:
                    shutil.copy2(child, target)
        except Exception:
            restore_backup(install_dir, backup)
            raise

        if restart:
            launched = subprocess.Popen([str(install_dir / "SaenaeOrderManager.exe")], cwd=str(install_dir))
            time.sleep(8)
            if launched.poll() is not None:
                restore_backup(install_dir, backup)
                # Try to reopen the restored version for the user.
                subprocess.Popen([str(install_dir / "SaenaeOrderManager.exe")], cwd=str(install_dir))
                raise RuntimeError("업데이트 후 프로그램이 정상 실행되지 않아 이전 버전으로 복구했습니다.")
        # Keep rollback copy for manual recovery; clean staging only.
        shutil.rmtree(staging, ignore_errors=True)
        print(f"업데이트 완료. 롤백 백업: {backup}")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Order Manager Updater")
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()
    try:
        replace_installation(Path(args.install_dir), Path(args.package), args.sha256, args.pid, not args.no_restart)
        return 0
    except Exception as exc:
        print(f"업데이트 실패: {exc}", file=sys.stderr)
        input("기존 프로그램 복구 여부를 확인한 후 Enter를 누르세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
