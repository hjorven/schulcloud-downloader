"""Speichert Basis-URL und Zugangs-Token lokal (nur für den eigenen Benutzer lesbar)."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://brandenburg.cloud"


def config_dir() -> Path:
    override = os.environ.get("SC_DL_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "sc-dl"


def config_file() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    base_url: str = DEFAULT_BASE_URL
    token: str | None = None
    api_v1_prefix: str = "/api/v1"

    @classmethod
    def load(cls) -> "Config":
        path = config_file()
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            base_url=data.get("base_url", DEFAULT_BASE_URL),
            token=data.get("token"),
            api_v1_prefix=data.get("api_v1_prefix", "/api/v1"),
        )

    def save(self) -> Path:
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"base_url": self.base_url, "token": self.token, "api_v1_prefix": self.api_v1_prefix},
            indent=2,
        )
        # Datei zuerst mit restriktiven Rechten anlegen, dann schreiben.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path

    def clear_token(self) -> None:
        self.token = None
        self.save()


def token_expiry(token: str) -> float | None:
    """Liest den Ablaufzeitpunkt (exp) aus einem JWT, ohne die Signatur zu prüfen."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except (IndexError, ValueError, json.JSONDecodeError):
        return None


def token_is_expired(token: str, now: float | None = None) -> bool:
    exp = token_expiry(token)
    if exp is None:
        return False
    return (now if now is not None else time.time()) >= exp
