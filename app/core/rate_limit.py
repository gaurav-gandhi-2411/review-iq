from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

# Module-level singleton — imported by demo.py, signup.py, and main.py.
# default_limits read from settings at import time; get_settings() is lru_cached.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{get_settings().rate_limit_per_minute}/minute"],
)
