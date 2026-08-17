"""Application configuration via Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM providers
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")

    # Auth
    api_key: str = Field(default="", alias="API_KEY")

    # Hugging Face
    hf_token: str = Field(default="", alias="HF_TOKEN")

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/review_iq.db",
        alias="DATABASE_URL",
    )

    # App
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_review_length: int = Field(default=5000, alias="MAX_REVIEW_LENGTH")
    rate_limit_per_minute: int = Field(default=30, alias="RATE_LIMIT_PER_MINUTE")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # LLM model names
    # Groq deprecated llama-3.3-70b-versatile on 2026-08-16 (console.groq.com/docs/
    # deprecations); openai/gpt-oss-120b is their documented replacement, same tier.
    groq_model: str = Field(
        default="openai/gpt-oss-120b",
        alias="GROQ_MODEL",
    )
    # gemini-2.0-flash is shut down (ai.google.dev/gemini-api/docs/models, "Previous
    # models"); gemini-2.5-flash is the same cost/speed tier's current stable offering.
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        alias="GEMINI_MODEL",
    )

    # Extraction limits
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    llm_timeout_seconds: int = Field(default=30, alias="LLM_TIMEOUT_SECONDS")

    # Bulk-path Groq throttling (Option A of the CSV-throttling fix, 2026-07-09): bounds
    # batch/CSV extraction at the Groq CALL layer so retry/escalation bursts cannot starve
    # interactive /v2/extract on the shared key (2026-07-07 incident). At ~1800 tokens/call
    # against the small model's 6000 TPM budget, ~3.3 calls/min is sustainable — 2/min for
    # bulk leaves headroom for interactive traffic.
    bulk_llm_calls_per_minute: float = Field(default=2.0, alias="BULK_LLM_CALLS_PER_MINUTE")
    bulk_llm_max_concurrency: int = Field(default=1, alias="BULK_LLM_MAX_CONCURRENCY")

    # Deployment target — controls which routers are mounted
    deploy_target: Literal["hf-spaces", "cloud-run", "local"] = Field(
        default="local", alias="DEPLOY_TARGET"
    )

    # Wave 1 S0 remediation (ADR 0006): which Cloud Run service this process is. "public"
    # (default) mounts everything except admin_router; "admin" mounts ONLY ops_router +
    # admin_router and is deployed --no-allow-unauthenticated (Cloud Run IAM gates all
    # network reachability).
    #
    # Bug fixed 2026-08-01: this comment previously claimed the public service
    # "authenticates to Postgres as review_iq_app (no BYPASSRLS)" and the admin service
    # "authenticates as review_iq_admin (BYPASSRLS) via admin_database_url below". Neither
    # was true at the time it was written -- verified directly against the live database
    # and the live Cloud Run config, not assumed: review_iq_app held BYPASSRLS (RLS
    # provided zero protection to any request-serving path that didn't explicitly call
    # _set_tenant() first), review_iq_admin did not exist as a role at all, and
    # admin_database_url pointed at the exact same Secret Manager secret as the public
    # service's supabase_database_url -- there was no actual role separation between the
    # two services. A wrong security comment is worse than none; this is why the comment
    # is being corrected in the same change that fixes the underlying grant, not left to
    # rot further. See supabase/migrations/20260801000001_role_separation_bypassrls_
    # remediation.sql and ops/runbooks/bypassrls-remediation-cutover.md for the actual fix
    # and its cutover sequence -- consult those, not this comment's prose, for the
    # CURRENT state of which role either service authenticates as until that cutover is
    # complete and this comment is updated again to describe it as fact rather than intent.
    service_role: Literal["public", "admin"] = Field(default="public", alias="SERVICE_ROLE")

    # Admin HTTP Basic auth
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password_hash: str = Field(default="", alias="ADMIN_PASSWORD_HASH")
    # LLM privacy: set True only on v1/demo path; v2 org-key path is always Groq-only
    enable_gemini_fallback: bool = Field(default=False, alias="ENABLE_GEMINI_FALLBACK")

    # Tiered routing: en-only small tier; hi+hi-en routed to large.
    # Enabled after v0.5.1 routed eval: en 86.2 / hi 86.1 / hi-en 83.6 / overall 85.3%.
    enable_tiered_routing: bool = Field(default=True, alias="ENABLE_TIERED_ROUTING")

    # Phase 2 batch-defect detector (app/core/detectors/batch_defect.py) -- off by default.
    # SYNTHETIC-VALIDATED, not yet proven on real seller data (see project memory). Gates both
    # the on-demand GET endpoint AND the scheduled detector sweep (app/core/alerts/detector_sweep.py).
    enable_batch_defect_detector: bool = Field(default=False, alias="ENABLE_BATCH_DEFECT_DETECTOR")
    # Phase 2 fake-campaign detector (app/core/detectors/campaign.py) -- off by default.
    # SYNTHETIC-VALIDATED, stress-tested, not yet proven on real seller data. Reviewer-identity
    # signal is stubbed to 0 (no ingestion path captures it yet) -- see that module's docstring
    # for the accepted recall-only limitation this implies. Gates the scheduled detector sweep.
    enable_fake_campaign_detector: bool = Field(
        default=False, alias="ENABLE_FAKE_CAMPAIGN_DETECTOR"
    )

    # Tiered model names — both Groq (privacy-vetted)
    # Both deprecated by Groq on 2026-08-16 (console.groq.com/docs/deprecations); replaced
    # with their documented successors, same fast/cheap vs. stronger tiering intact.
    groq_model_small: str = Field(
        default="openai/gpt-oss-20b",
        alias="GROQ_MODEL_SMALL",
    )
    groq_model_large: str = Field(
        default="openai/gpt-oss-120b",
        alias="GROQ_MODEL_LARGE",
    )

    # Secondary failover provider — must be a no-train provider when configured
    secondary_provider_api_key: str = Field(default="", alias="SECONDARY_PROVIDER_API_KEY")
    secondary_provider_model: str = Field(default="", alias="SECONDARY_PROVIDER_MODEL")

    # CORS allowlist — comma-separated origins (env: ALLOWED_ORIGINS).
    # Default covers local dev: both localhost and 127.0.0.1 aliases on :5173
    # (canonical Vite port) and :5174 (Vite fallback when :5173 is occupied).
    # Browsers treat all four as distinct origins. Vite is pinned to :5173 via
    # vite.config.ts server.strictPort, but :5174 aliases keep local dev working
    # during the transition window. Production Cloud Run must set ALLOWED_ORIGINS
    # to the locked public web-app origin(s) ONLY — no localhost, no 127.0.0.1,
    # no wildcard.
    # Wildcard ("*") must never appear here — use an explicit list always.
    #
    # Stored as a raw string because pydantic_settings JSON-decodes list[str] fields
    # before validators run, which breaks comma-separated env var syntax.
    # Use the allowed_origins property everywhere.
    allowed_origins_env: str = Field(
        default=(
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost:5174,http://127.0.0.1:5174,"
            "https://review-iq-demo.pages.dev"
        ),
        alias="ALLOWED_ORIGINS",
    )

    @property
    def allowed_origins(self) -> list[str]:
        origins = [o.strip() for o in self.allowed_origins_env.split(",") if o.strip()]
        if "*" in origins:
            # Runtime fail-closed backstop: previously only test_cors_policy.py caught a
            # wildcard ALLOWED_ORIGINS before it could ship. This makes it impossible to
            # boot the app with wildcard CORS at all, not just impossible to merge.
            raise ValueError("ALLOWED_ORIGINS must not contain '*' — use an explicit origin list.")
        return origins

    # Shopify connector
    # Register app at partners.shopify.com to get these credentials.
    # Scopes required: write_product_reviews read_metaobjects read_products read_customers
    shopify_client_id: str = Field(default="", alias="SHOPIFY_CLIENT_ID")
    shopify_client_secret: str = Field(default="", alias="SHOPIFY_CLIENT_SECRET")
    shopify_api_version: str = Field(default="2024-10", alias="SHOPIFY_API_VERSION")
    # Fernet key for encrypting Shopify OAuth tokens at rest in shopify_installations.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Dev: .env.local (gitignored). Prod: Google Secret Manager.
    shopify_token_encryption_key: str = Field(default="", alias="SHOPIFY_TOKEN_ENCRYPTION_KEY")
    # Base URL of the deployed API — used as webhook callback address on install and as
    # redirect_uri in the OAuth begin flow. Dev: set to an ngrok tunnel URL.
    # Prod: https://<cloud-run-service-url> (or custom API domain once configured).
    shopify_webhook_base_url: str = Field(default="", alias="SHOPIFY_WEBHOOK_BASE_URL")

    # Google Business Profile connector
    # Register a GCP project + OAuth client at console.cloud.google.com to get these.
    # Business Profile APIs require Google's manual access approval (see
    # app/core/ingestion/google_business_source.py module docstring for the checklist).
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    # Fernet key for encrypting the long-lived Google refresh_token at rest in
    # google_business_installations. Generate the same way as shopify_token_encryption_key.
    google_token_encryption_key: str = Field(default="", alias="GOOGLE_TOKEN_ENCRYPTION_KEY")
    # Base URL of the deployed API — used as redirect_uri in the OAuth begin flow.
    # Dev: ngrok tunnel URL. Prod: https://<cloud-run-service-url> (or custom API domain).
    google_webhook_base_url: str = Field(default="", alias="GOOGLE_WEBHOOK_BASE_URL")
    # Cloud Pub/Sub topic that receives NEW_REVIEW notifications from Google; registered
    # per-account via the Business Profile notificationSetting API on install.
    google_pubsub_topic: str = Field(default="", alias="GOOGLE_PUBSUB_TOPIC")
    # Shared-secret query token verified on the Pub/Sub push endpoint
    # (/webhooks/google/reviews?token=...) via hmac.compare_digest before any parsing.
    google_pubsub_push_token: str = Field(default="", alias="GOOGLE_PUBSUB_PUSH_TOKEN")

    # Shared-secret header token protecting POST /internal/digest/run (timing-safe
    # compare via hmac.compare_digest), mirroring GOOGLE_PUBSUB_PUSH_TOKEN's pattern.
    digest_trigger_token: str = Field(default="", alias="DIGEST_TRIGGER_TOKEN")

    # Shared-secret header token protecting POST /internal/ingest/tick (timing-safe
    # compare via hmac.compare_digest) — same pattern as digest_trigger_token. Plain
    # env var, not Secret Manager: same precedent as DIGEST_TRIGGER_TOKEN, the project
    # is already at its Secret Manager free-tier ceiling (6/6 secrets).
    ingest_tick_token: str | None = Field(default=None, alias="INGEST_TICK_TOKEN")
    # Rows drained per tick (Option B of the CSV-throttling fix, 2026-07-09). At
    # 3 rows/tick x 1 tick/min via Cloud Scheduler, this roughly matches the Option A
    # bulk lane's own throughput (~2 Groq calls/min, app/core/ratelimit.py) so the
    # tick worker can't outrun the throttle it shares with interactive traffic.
    ingest_tick_rows: int = Field(default=3, alias="INGEST_TICK_ROWS")

    # Shared-secret header token protecting POST /internal/detectors/run (timing-safe compare
    # via hmac.compare_digest) — same pattern as digest_trigger_token/ingest_tick_token. Plain
    # env var, not Secret Manager, same reason: the project is already at its Secret Manager
    # free-tier ceiling.
    detector_sweep_trigger_token: str = Field(default="", alias="DETECTOR_SWEEP_TRIGGER_TOKEN")

    # Resend transactional email
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    resend_from_email: str = Field(default="", alias="RESEND_FROM_EMAIL")
    # Sandbox: only delivers to this verified address; real-recipient delivery
    # requires a verified sending domain in the Resend dashboard.
    resend_test_recipient: str = Field(default="", alias="RESEND_TEST_RECIPIENT")
    # Display name on the From header (e.g. "Samidha Reviews"). Combined with
    # resend_from_email as "Name <email>". Switching sandbox -> custom domain
    # is env-only: RESEND_FROM_EMAIL + this + a Resend domain-verify, no code change.
    resend_from_name: str = Field(default="Samidha Reviews", alias="RESEND_FROM_NAME")
    # Optional Reply-To so seller replies don't bounce against a noreply sender.
    resend_reply_to: str = Field(default="", alias="RESEND_REPLY_TO")
    # Toggle the leading emoji (e.g. "⚠️") on alert subject lines without a code
    # change — emoji can nudge spam filters either way; flip this to A/B test
    # inbox placement for a given sending domain.
    alert_subject_emoji_enabled: bool = Field(default=True, alias="ALERT_SUBJECT_EMOJI_ENABLED")

    # HMAC signing key for one-click unsubscribe links embedded in alert emails
    # (GET/POST /unsubscribe). Unset disables the unsubscribe link and the
    # List-Unsubscribe header entirely — emails still send, just without them.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    unsubscribe_signing_key: str = Field(default="", alias="UNSUBSCRIBE_SIGNING_KEY")
    # Public base URL of the deployed API, used to build the absolute unsubscribe
    # link in alert emails (e.g. https://<cloud-run-service>.run.app). Dev: ngrok
    # tunnel URL, same as shopify_webhook_base_url / google_webhook_base_url.
    api_public_base_url: str = Field(default="", alias="API_PUBLIC_BASE_URL")

    # Supabase
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_db_password: str = Field(default="", alias="SUPABASE_DB_PASSWORD")
    # Pooler (port 6543, transaction mode) — default for all app traffic
    supabase_database_url: str = Field(default="", alias="SUPABASE_DATABASE_URL")
    # Direct (port 5432) — migrations and integration tests only (session-level GUCs)
    supabase_direct_url: str = Field(default="", alias="SUPABASE_DIRECT_URL")
    # Wave 1 S0 remediation (ADR 0006): review_iq_admin's own DSN, used ONLY by
    # app/api/admin.py's _db_connect() when SERVICE_ROLE=admin. Distinct Secret Manager
    # secret from supabase_database_url — never present in the public service's env.
    admin_database_url: str = Field(default="", alias="ADMIN_DATABASE_URL")


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
