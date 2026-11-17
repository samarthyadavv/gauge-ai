"""Run the web app and queue worker on one hosted instance."""

import os
import signal
import subprocess
import sys
import threading


def main():
    shutdown = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: shutdown.set())

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    if shutdown.is_set():
        return 0

    commands = [
        [sys.executable, "-m", "app.worker"],
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "0.0.0.0", "--port", os.getenv("PORT", "9200"),
            "--no-access-log",
        ],
    ]
    children = []
    try:
        for command in commands:
            children.append(subprocess.Popen(command))
        while not shutdown.wait(0.25):
            for child in children:
                if child.poll() is not None:
                    print(f"Hosted process stopped unexpectedly (status {child.returncode})", file=sys.stderr)
                    return 1
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    sys.exit(main())
