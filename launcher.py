"""Frozen-app launcher for DynaCalendar (PyInstaller entry point).

Why this file exists: `python -m app.main` works from a source checkout, but a
frozen exe cannot write next to itself (a --onefile build unpacks to a temp dir
that is wiped on exit) and must not assume port 8000 is free. This launcher:

  1. points the database at %LOCALAPPDATA%\\DynaCalendar (survives restarts)
  2. copies the bundled sample files there on first run
  3. picks the first free port from 8000 upward
  4. opens the browser once the server is actually listening

Nothing here changes app behaviour: it imports the exact same FastAPI app.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _bundle_dir() -> Path:
    """Directory holding bundled data (PyInstaller temp dir when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _app_data_dir() -> Path:
    """Portable first: keep everything beside the exe, like a portable app.

    A frozen build may sit in a read-only folder (or a locked-down profile), so
    fall back to %LOCALAPPDATA% and finally the system temp dir.
    """
    import tempfile

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "DynaCalendar-data")
    candidates.append(Path(__file__).resolve().parent / "data")
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        candidates.append(Path(root) / "DynaCalendar")
    candidates.append(Path(tempfile.gettempdir()) / "DynaCalendar")

    for path in candidates:
        if _writable(path):
            return path
    raise SystemExit(
        "DynaCalendar: no writable folder found for its database.\n"
        "Move the DynaCalendar folder somewhere you can write (e.g. Desktop) and retry."
    )


def _free_port(start: int = 8000, attempts: int = 20) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"No free port in {start}-{start + attempts - 1}")


def _prepare() -> tuple[Path, Path]:
    data_dir = _app_data_dir()
    os.environ["DYNACALENDAR_DB"] = str(data_dir / "dynacalendar.db")

    samples_src = _bundle_dir() / "samples"
    samples_dst = data_dir / "samples"
    if samples_src.is_dir() and not samples_dst.exists():
        try:
            shutil.copytree(samples_src, samples_dst)
        except OSError:
            pass
    return data_dir, samples_dst


def _purge_stale_mei() -> None:
    """Remove leftovers from earlier runs.

    A --onefile build unpacks into ``_MEIxxxx`` next to the exe (see
    --runtime-tmpdir "."). Windows does not always run the bootloader's cleanup
    when the console is closed, so each run could leave ~120 MB behind. Anything
    in use by a live instance is locked by the OS and simply fails to delete —
    that is why the errors below are ignored on purpose.
    """
    if not getattr(sys, "frozen", False):
        return
    folder = Path(sys.executable).resolve().parent
    current = getattr(sys, "_MEIPASS", "")
    for stale in folder.glob("_MEI*"):
        if not stale.is_dir() or str(stale) == current:
            continue
        try:
            shutil.rmtree(stale)
        except OSError:
            pass


def main() -> None:
    _purge_stale_mei()
    data_dir, samples_dir = _prepare()

    import uvicorn  # imported late so the path/env above are already set
    from app.main import app

    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    print("=" * 62)
    print("  DynaCalendar")
    print(f"  URL      : {url}")
    print(f"  Data     : {data_dir}")
    if samples_dir.is_dir():
        print(f"  Samples  : {samples_dir}")
        print("             (use these in Import .ics / the drop zone)")
    print("  Close this window to stop the app.")
    print("=" * 62, flush=True)

    def _open_when_ready() -> None:
        for _ in range(100):
            time.sleep(0.2)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.3)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    webbrowser.open(url)
                    return

    threading.Thread(target=_open_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
