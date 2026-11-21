"""Launcher: start the local server on 127.0.0.1 and open the app window (pywebview)."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import socket
import sys
import threading
import time

import uvicorn

from fotoduenner import __version__
from fotoduenner.api.server import create_app
from fotoduenner.logging_setup import setup_logging

log = logging.getLogger("fotoduenner.launcher")

HOST = "127.0.0.1"  # never bind to anything else (CLAUDE.md rule 3)
WINDOW_TITLE = "FotoDünner"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def start_server(port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(create_app(), host=HOST, port=port, log_config=None, access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Server did not start")
        time.sleep(0.02)
    return server, thread


def run_window(url: str, smoke_test: bool) -> None:
    import webview  # imported lazily so --no-window works without a GUI stack

    window = webview.create_window(
        WINDOW_TITLE, url, width=1280, height=820, min_size=(1024, 640), background_color="#0b0d12"
    )

    def smoke(win: webview.Window) -> None:
        # Wait for the page, report its title, then close the window.
        win.events.loaded.wait(20)
        print(f"SMOKE page title: {win.evaluate_js('document.title')}", flush=True)
        win.destroy()

    if smoke_test:
        webview.start(smoke, window, gui="edgechromium")
    else:
        webview.start(gui="edgechromium")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fotoduenner")
    parser.add_argument("--no-window", action="store_true", help="only run the server")
    parser.add_argument("--smoke-test", action="store_true", help="open window, then close")
    args = parser.parse_args(argv)

    setup_logging()
    port = find_free_port()
    server, thread = start_server(port)
    url = f"http://{HOST}:{port}/"
    log.info("FotoDünner %s serving on %s", __version__, url)
    print(f"FotoDünner {__version__} on {url}", flush=True)

    try:
        if args.no_window:
            thread.join()
        else:
            run_window(url, args.smoke_test)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        log.info("FotoDünner stopped")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # required for PyInstaller + spawn (02 §5)
    sys.exit(main())
