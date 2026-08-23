import os
from typing import Optional

from .base import LLMProvider
from .gemini import GeminiProvider

_provider: Optional[LLMProvider] = None


def get_provider() -> LLMProvider:
    """Return the configured LLM provider (LLM_PROVIDER env var, default gemini).

    New providers (Claude, GPT, ...) register here — the rest of the app only
    ever sees the LLMProvider interface.
    """
    global _provider
    if _provider is None:
        name = os.getenv("LLM_PROVIDER", "gemini").lower()
        if name == "gemini":
            _provider = GeminiProvider()
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {name}")
    return _provider
