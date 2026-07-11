from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

# Module-level singleton — imported by demo.py, signup.py, and main.py.
# default_limits read from settings at import time; get_settings() is lru_cached.
#
# KNOWN LIMITATION (audit finding #10, assessed 2026-07-11, deferred on purpose):
# slowapi's default storage is in-process memory, not shared across replicas. Cloud
# Run's review-iq service runs with maxScale=3, so under concurrent load the
# practical aggregate limit for e.g. /demo/extract's "5/minute" can be up to ~3x
# the configured value (each instance enforces its own independent 5/minute) --
# NOT unbounded, but not exactly what the number says either. Live-tested
# 2026-07-11: a single client CANNOT bypass this via a spoofed X-Forwarded-For
# (get_remote_address only reads the real TCP peer, confirmed both in code and
# empirically against prod) -- the gap is instance-count multiplication under
# real concurrent load, not a header-spoofing exploit.
#
# Deferred rather than fixed now: a real fix needs either a shared backend
# (Redis via slowapi's storage_uri, a new infra dependency + cost pre-launch) or
# maxScale=1 (eliminates the multiplication but trades away Cloud Run's ability to
# scale out under real traffic -- an availability/cost tradeoff of its own, not a
# free fix). No evidence of live abuse as of this writing. Revisit if either (a)
# real usage data shows this mattering, or (b) a shared cache is added for some
# other reason and this can ride along on it.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{get_settings().rate_limit_per_minute}/minute"],
)
