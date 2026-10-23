"""API configuration from environment variables."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ["DATABASE_URL"])
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip() for o in os.environ.get("LIVESIM_CORS_ORIGINS", "*").split(",") if o.strip()
        )
    )
    keepalive_seconds: float = field(
        default_factory=lambda: float(os.environ.get("LIVESIM_SSE_KEEPALIVE_SECONDS", "15"))
    )
