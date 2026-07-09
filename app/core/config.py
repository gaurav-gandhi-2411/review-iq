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
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        alias="GROQ_MODEL",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
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

    # Admin HTTP Basic auth
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password_hash: str = Field(default="", alias="ADMIN_PASSWORD_HASH")
    # LLM privacy: set True only on v1/demo path; v2 org-key path is always Groq-only
    enable_gemini_fallback: bool = Field(default=False, alias="ENABLE_GEMINI_FALLBACK")

    # Tiered routing: en-only small tier; hi+hi-en routed to large.
    # Enabled after v0.5.1 routed eval: en 86.2 / hi 86.1 / hi-en 83.6 / overall 85.3%.
    enable_tiered_routing: bool = Field(default=True, alias="ENABLE_TIERED_ROUTING")

    # Tiered model names — both Groq (privacy-vetted)
    groq_model_small: str = Field(
        default="llama-3.1-8b-instant",
        alias="GROQ_MODEL_SMALL",
    )
    groq_model_large: str = Field(
        default="llama-3.3-70b-versatile",
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
        return [o.strip() for o in self.allowed_origins_env.split(",") if o.strip()]

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


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
