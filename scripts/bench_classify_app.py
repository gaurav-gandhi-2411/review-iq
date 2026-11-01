"""LOCAL BENCH ONLY: the real app with API-key auth replaced by a fixed context, so a local container can be driven without
Postgres. Mounted into the container at run time (never baked into the image, never imported by the real app):

    docker run -v <repo>/scripts/bench_classify_app.py:/bench/bench_classify_app.py:ro -e PYTHONPATH=/app:/bench \
        ... review-iq-classify:<tag> uvicorn bench_classify_app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

from app.auth.api_key import ApiKeyContext, require_api_key
from app.main import create_app

app = create_app()
app.dependency_overrides[require_api_key] = lambda: ApiKeyContext(
    org_id="bench-org", api_key_id="bench", key_name="bench", usage_record_id=""
)
