"""Supervise web + one bot in a single Raven Host process allocation."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    commands = [
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "17563"],
        [sys.executable, "-m", "bot.main"],
    ]
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    exit_code = 0
    try:
        for command in commands:
            children.append(subprocess.Popen(command))
        while not stopping:
            for child in children:
                if child.poll() is not None:
                    exit_code = child.returncode or 1
                    stopping = True
                    break
            time.sleep(0.5)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
