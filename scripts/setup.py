"""Generate local secrets without printing them. Standard library only."""

import os, secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
(root / "data").mkdir(exist_ok=True)
path = root / ".env"
values = {
    "ADMIN_KEY": secrets.token_urlsafe(40),
    "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
    "DATABASE_URL": "sqlite:///" + str(root / "data/gauge.db"),
    "BASE_URL": "http://localhost:9200",
    "ENVIRONMENT": "development",
    "OPENAI_API_KEY": "",
    "OPENAI_MODELS": "gpt-4.1-mini",
}
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in values.items()))
except FileExistsError:
    raise SystemExit(".env already exists; refusing to overwrite secrets")
print(
    "Created .env with private permissions. Copy ADMIN_KEY from that file into the local sign-in form. Never commit or share it."
)
