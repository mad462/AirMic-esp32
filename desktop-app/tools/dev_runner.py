from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


WATCH_DIRS = ("app", "core", "services")
POLL_INTERVAL_S = 0.5


def collect_python_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        files.extend(path for path in root.rglob("*.py") if path.is_file())
    return sorted(set(files))


def snapshot_mtimes(files: list[Path]) -> dict[Path, float]:
    mtimes: dict[Path, float] = {}
    for file_path in files:
        try:
            mtimes[file_path] = file_path.stat().st_mtime
        except FileNotFoundError:
            mtimes[file_path] = -1.0
    return mtimes


def has_changes(before: dict[Path, float], after: dict[Path, float]) -> bool:
    if before.keys() != after.keys():
        return True
    for file_path, old_mtime in before.items():
        if after.get(file_path) != old_mtime:
            return True
    return False


def launch_app(project_root: Path) -> subprocess.Popen[str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        cwd=str(project_root),
        creationflags=creationflags,
    )


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def run_dev_loop(project_root: Path) -> int:
    roots = [project_root / name for name in WATCH_DIRS]
    tracked_files = collect_python_files(roots)
    last_snapshot = snapshot_mtimes(tracked_files)

    print("[AirMic Dev] starting app with auto-restart")
    process = launch_app(project_root)

    try:
        while True:
            time.sleep(POLL_INTERVAL_S)
            tracked_files = collect_python_files(roots)
            current_snapshot = snapshot_mtimes(tracked_files)

            if has_changes(last_snapshot, current_snapshot):
                print("[AirMic Dev] file change detected, restarting GUI...")
                terminate_process(process)
                process = launch_app(project_root)
                last_snapshot = current_snapshot
                continue

            if process.poll() is not None:
                exit_code = process.returncode or 0
                print(f"[AirMic Dev] GUI exited with code {exit_code}")
                if exit_code == 0:
                    terminate_process(process)
                    return 0
                print("[AirMic Dev] restarting after crash...")
                process = launch_app(project_root)
                last_snapshot = current_snapshot
    except KeyboardInterrupt:
        print("\n[AirMic Dev] stopping dev runner")
        terminate_process(process)
        return 0


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    return run_dev_loop(project_root)


if __name__ == "__main__":
    raise SystemExit(main())