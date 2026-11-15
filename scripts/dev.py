"""Run with: uv run --project backend python scripts/dev.py"""

import os, subprocess, sys, signal
from pathlib import Path
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
if not (root / ".env").exists():
    raise SystemExit("Run python scripts/setup.py first")
env = os.environ.copy()
env.update({k: v for k, v in dotenv_values(root / ".env").items() if v is not None})
subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    cwd=root / "backend",
    env=env,
    check=True,
)
children = [
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "9200",
            "--no-access-log",
        ],
        cwd=root / "backend",
        env=env,
    ),
    subprocess.Popen(
        [sys.executable, "-m", "app.worker"], cwd=root / "backend", env=env
    ),
]


def stop(*args):
    for child in children:
        child.terminate()
    for child in children:
        try:
            child.wait(timeout=45)
        except subprocess.TimeoutExpired:
            child.kill()
    raise SystemExit()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
print(
    "Gauge is starting at http://localhost:9200. Use ADMIN_KEY from your local .env to sign in."
)
try:
    children[0].wait()
finally:
    stop()
