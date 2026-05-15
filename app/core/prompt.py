"""Backward-compatibility shim — use app.core.prompts instead."""

from app.core.prompts import PROMPT_VERSION  # noqa: F401
from app.core.prompts.en import build_prompt as build_user_prompt  # noqa: F401
