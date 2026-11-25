"""Launcher: single instance, local server on 127.0.0.1, app window (pywebview, D4/A3)."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import socket
import sys
import threading
import time

import uvicorn

from fotoduenner import __version__
from fotoduenner.api.security import new_token
from fotoduenner.api.server import create_app
from fotoduenner.api.state import AppState
from fotoduenner.logging_setup import setup_logging
from fotoduenner.paths import data_dir
from fotoduenner.winplatform import (
    WEBVIEW2_DOWNLOAD_URL,
    ask_yes_no,
    message_box,
    webview2_installed,
)

log = logging.getLogger("fotoduenner.launcher")

HOST = "127.0.0.1"  # never bind to anything else (CLAUDE.md rule 3)
WINDOW_TITLE = "FotoDünner"
BACKGROUND = "#04090d"
SERVER_START_TIMEOUT_S = 15.0
GRACEFUL_SHUTDOWN_S = 2

MSG_NO_WEBVIEW2 = (
    "FotoDünner benötigt die „Microsoft Edge WebView2 Runtime“, die auf diesem Rechner fehlt.\n\n"
    "Sie ist kostenlos und auf Windows 11 normalerweise vorhanden. Soll die Download-Seite von "
    "Microsoft im Browser geöffnet werden?"
)
MSG_CONFIRM_CLOSE_MOVING = (
    "Es werden gerade Bilder verschoben.\n\nTrotzdem beenden? Der Vorgang hält nach dem "
    "aktuellen Bild an. Alles bisher Verschobene ist protokolliert und lässt sich fortsetzen "
    "oder rückgängig machen."
)
MSG_CONFIRM_CLOSE = (
    "Die Analyse läuft noch.\n\nTrotzdem beenden? Bereits analysierte Bilder bleiben gespeichert; "
    "beim nächsten Öffnen geht es dort weiter."
)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def start_server(app: object, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(
        app,  # type: ignore[arg-type]
        host=HOST,
        port=port,
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,  # open SSE streams must not block exit
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + SERVER_START_TIMEOUT_S
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Server did not start")
        time.sleep(0.02)
    return server, thread


def say(message: str) -> None:
    """Log, and print when a console exists (a windowed PyInstaller build has no stdout)."""
    log.info(message)
    if sys.stdout is not None:
        print(message, flush=True)


def run_window(url: str, state: AppState, smoke_test: bool) -> None:
    import webview  # imported lazily so --no-window works without a GUI stack

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=1280,
        height=820,
        min_size=(1024, 640),
        background_color=BACKGROUND,
    )

    def pick_folder() -> str | None:
        result = window.create_file_dialog(webview.FileDialog.FOLDER)
        return result[0] if result else None

    def focus() -> None:
        window.restore()
        window.show()
        window.on_top = True  # the usual trick to get past Windows' focus-stealing guard
        window.on_top = False

    def on_closing() -> bool:
        if smoke_test or not state.busy():
            return True
        message = MSG_CONFIRM_CLOSE_MOVING if state.moving() else MSG_CONFIRM_CLOSE
        return ask_yes_no(message)  # False keeps the window open

    state.folder_picker = pick_folder
    state.on_focus = focus
    state.open_folder = os.startfile  # Explorer on _Aussortiert (04 §3)
    state.on_shutdown = window.destroy
    window.events.closing += on_closing

    def smoke(win: webview.Window) -> None:
        win.events.loaded.wait(20)
        say(f"SMOKE page title: {win.evaluate_js('document.title')}")
        win.destroy()

    if smoke_test:
        webview.start(smoke, window, gui="edgechromium")
    else:
        webview.start(gui="edgechromium")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fotoduenner")
    parser.add_argument("--no-window", action="store_true", help="only run the server")
    parser.add_argument("--smoke-test", action="store_true", help="open window, then close")
    parser.add_argument("--self-check", action="store_true", help="load all models, then exit")
    args = parser.parse_args(argv)

    setup_logging()
    if args.self_check:
        from fotoduenner.selfcheck import run_selfcheck

        problems = run_selfcheck()
        say("SELFCHECK ok" if not problems else f"SELFCHECK failed: {problems}")
        return 0 if not problems else 1

    from fotoduenner import instance

    lock = instance.acquire(data_dir())
    if lock is None:
        focused = instance.focus_running_instance(data_dir())
        say("FotoDünner already running" + (", window brought to front" if focused else ""))
        return 0

    if not args.no_window and not webview2_installed():
        if ask_yes_no(MSG_NO_WEBVIEW2):
            os.startfile(WEBVIEW2_DOWNLOAD_URL)  # opens the user's browser; we do not download
        lock.release()
        return 1

    state = AppState()
    token = new_token()
    port = find_free_port()
    app = create_app(state, token, allowed_hosts={f"{HOST}:{port}"})
    server, thread = start_server(app, port)
    lock.publish(port, token)
    url = f"http://{HOST}:{port}/?t={token}"
    say(f"FotoDünner {__version__} on http://{HOST}:{port}/")

    try:
        if args.no_window:
            stop = threading.Event()
            state.on_shutdown = stop.set
            say(f"open {url}")
            while thread.is_alive() and not stop.wait(0.5):
                pass
        else:
            run_window(url, state, args.smoke_test)
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("window failed")
        message_box("FotoDünner konnte das Programmfenster nicht öffnen. Details im Protokoll.")
        return 1
    finally:
        state.shutdown()
        server.should_exit = True
        thread.join(timeout=GRACEFUL_SHUTDOWN_S + 3)
        lock.release()
        log.info("FotoDünner stopped")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # required for PyInstaller + spawn (02 §5)
    sys.exit(main())
