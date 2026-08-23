from abc import ABC, abstractmethod
from typing import AsyncIterator


class LLMProvider(ABC):
    """Minimal provider interface for the model router.

    messages: [{"role": "user" | "assistant", "content": str}, ...]
    Yields response text chunks as they stream from the provider.
    """

    @abstractmethod
    def stream_chat(
        self, system: str, messages: list[dict]
    ) -> AsyncIterator[str]: ...
