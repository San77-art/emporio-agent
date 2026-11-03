"""Runtime configuration (environment variables, optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
STORE_TZ = "America/Campo_Grande"  # the store is in Campo Grande/MS (UTC-4, no DST)

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-4o-mini",
}


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (avoids a dependency). Real env vars win over the file."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    state_dir: Path
    provider: str
    model: str
    max_tokens: int
    temperature: float | None
    timezone: str = STORE_TZ
    now_override: str | None = None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> datetime:
        """Current time in store time. `STORE_NOW` pins it for reproducible demos/tests."""
        if self.now_override:
            dt = datetime.fromisoformat(self.now_override)
            return dt.replace(tzinfo=self.tz) if dt.tzinfo is None else dt.astimezone(self.tz)
        return datetime.now(self.tz)


def load_settings(**overrides) -> Settings:
    _load_dotenv(ROOT / ".env")
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"LLM_PROVIDER must be one of {sorted(DEFAULT_MODELS)}, got {provider!r}")

    temp_raw = os.environ.get("LLM_TEMPERATURE", "0.3").strip()
    values = dict(
        data_dir=Path(os.environ.get("EMPORIO_DATA_DIR", ROOT / "data")),
        state_dir=Path(os.environ.get("EMPORIO_STATE_DIR", ROOT / ".state")),
        provider=provider,
        model=os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODELS[provider],
        max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "1024")),
        temperature=float(temp_raw) if temp_raw else None,
        now_override=os.environ.get("STORE_NOW", "").strip() or None,
    )
    values.update(overrides)
    return Settings(**values)
