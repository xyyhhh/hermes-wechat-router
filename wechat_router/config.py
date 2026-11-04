from dataclasses import dataclass, field, fields
import math
from pathlib import Path
import re
from typing import Any

CATEGORIES = ("schedule", "deadline", "todo", "important", "other")
PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8768
    mode: str = "observe"
    owner_name: str = "微信账号本人"
    owner_ids: tuple[str, ...] = ()
    interests: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    timezone: str = "Asia/Shanghai"
    quiet_seconds: float = 8
    max_wait_seconds: float = 60
    poll_seconds: float = 1
    max_batch_messages: int = 12
    max_batch_chars: int = 12000
    max_context_chars: int = 6000
    max_context_messages: int = 20
    context_hours: float = 6
    max_candidates: int = 8
    candidate_days: float = 7
    min_confidence: float = 0.6
    max_attempts: int = 3
    retry_seconds: float = 15
    lease_seconds: float = 90
    request_timeout: float = 15
    cli_timeout: float = 30
    replay_seconds: float = 300
    max_body_bytes: int = 131072
    max_text_chars: int = 8000
    model: str = "jev-latest"
    board: str = "default"
    triage_profile: str = "inbox"
    routes: dict[str, str] = field(default_factory=lambda: {k: "inbox" for k in CATEGORIES})
    hermes_bin: str = "hermes"

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> "Settings":
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ValueError("platform extra must be an object")
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
        values = dict(raw)
        for key in ("owner_ids", "interests", "sources"):
            if key in values:
                if not isinstance(values[key], (list, tuple)) or any(
                    not isinstance(x, str) or not x.strip() for x in values[key]
                ):
                    raise ValueError(f"{key} must be a list of nonempty strings")
                values[key] = tuple(values[key])
        result = cls(**values)
        result.validate()
        return result

    def validate(self) -> None:
        if self.mode not in ("observe", "active"):
            raise ValueError("mode must be observe or active")
        if self.mode == "active" and not self.owner_ids:
            raise ValueError("active mode requires owner_ids for personal relevance judgments")
        for key in ("host", "owner_name", "timezone", "model", "hermes_bin"):
            if not isinstance(getattr(self, key), str) or not getattr(self, key).strip():
                raise ValueError(f"{key} must be a nonempty string")
        from zoneinfo import ZoneInfo
        ZoneInfo(self.timezone)
        for key in ("port", "max_batch_messages", "max_batch_chars", "max_context_chars",
                    "max_context_messages", "max_candidates", "max_attempts", "max_body_bytes", "max_text_chars"):
            v = getattr(self, key)
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ValueError(f"{key} must be a positive integer")
        for key in ("quiet_seconds", "max_wait_seconds", "poll_seconds", "context_hours", "candidate_days",
                    "retry_seconds", "lease_seconds", "request_timeout", "cli_timeout", "replay_seconds"):
            v = getattr(self, key)
            if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0:
                raise ValueError(f"{key} must be positive and finite")
        if not 1 <= self.port <= 65535 or self.max_batch_messages > 20 or self.max_candidates > 20:
            raise ValueError("port or batch/candidate limit out of range")
        if self.max_text_chars > self.max_batch_chars or self.quiet_seconds > self.max_wait_seconds:
            raise ValueError("text limit must fit batch; quiet window must fit max wait")
        if self.lease_seconds < 10:
            raise ValueError("lease_seconds must be at least 10")
        if isinstance(self.min_confidence, bool) or not isinstance(self.min_confidence, (int, float)) or not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if not isinstance(self.routes, dict) or set(self.routes) != set(CATEGORIES):
            raise ValueError("routes must define schedule, deadline, todo, important and other")
        for p in (*self.routes.values(), self.triage_profile):
            if not isinstance(p, str) or not PROFILE.fullmatch(p):
                raise ValueError("invalid profile name")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", self.board):
            raise ValueError("invalid board slug")


def data_path(home: Path) -> Path:
    return home / "plugin-data" / "wechat-router" / "router.sqlite3"
