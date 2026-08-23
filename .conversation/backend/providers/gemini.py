import asyncio
import hashlib
import logging
import os
from typing import AsyncIterator, Optional

from google import genai
from google.genai import types

from .base import LLMProvider

log = logging.getLogger(__name__)

# Gemini explicit caching requires a minimum context size (~1k tokens). Below
# this we just send the system prompt inline — caching would be rejected.
MIN_CACHE_CHARS = 8192
CACHE_TTL = os.getenv("GEMINI_CACHE_TTL", "3600s")


class GeminiProvider(LLMProvider):
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (add it to Replit Secrets)")
        self.client = genai.Client(api_key=api_key)
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.caching = os.getenv("GEMINI_CACHE", "1") != "0"
        self._cache_name: Optional[str] = None
        self._cache_key: Optional[str] = None
        self._cache_lock = asyncio.Lock()

    async def _get_cache(self, system: str) -> Optional[str]:
        """Cache the (large, stable) system prompt server-side and reuse it.

        Returns the cached-content name, or None to fall back to sending the
        system prompt inline. Keyed on model + prompt content, so editing the
        knowledge base transparently creates a new cache.
        """
        if not self.caching or len(system) < MIN_CACHE_CHARS:
            return None

        key = hashlib.sha256(f"{self.model}\0{system}".encode()).hexdigest()
        async with self._cache_lock:
            if self._cache_name and self._cache_key == key:
                return self._cache_name
            try:
                cache = await self.client.aio.caches.create(
                    model=self.model,
                    config=types.CreateCachedContentConfig(
                        system_instruction=system,
                        ttl=CACHE_TTL,
                        display_name="ask-weka-kb",
                    ),
                )
            except Exception as e:
                log.warning("Gemini context caching unavailable, sending inline: %s", e)
                self._cache_name = self._cache_key = None
                return None
            log.info("Created Gemini context cache %s (ttl=%s)", cache.name, CACHE_TTL)
            self._cache_name, self._cache_key = cache.name, key
            return cache.name

    async def stream_chat(
        self, system: str, messages: list[dict]
    ) -> AsyncIterator[str]:
        contents = [
            types.Content(
                role="user" if m["role"] == "user" else "model",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages
        ]

        cache_name = await self._get_cache(system)
        started = False
        try:
            async for chunk in self._stream(contents, system, cache_name):
                started = True
                yield chunk
        except Exception:
            # Only safe to retry before any text has reached the client,
            # otherwise the reply would be duplicated. A stale cache fails at
            # request setup, so that is the case this actually covers.
            if cache_name is None or started:
                raise
            log.warning("Cached request failed, retrying without cache")
            async with self._cache_lock:
                self._cache_name = self._cache_key = None
            async for chunk in self._stream(contents, system, None):
                yield chunk

    async def _stream(
        self, contents: list, system: str, cache_name: Optional[str]
    ) -> AsyncIterator[str]:
        if cache_name:
            # system_instruction lives inside the cache; passing it again is an error.
            config = types.GenerateContentConfig(cached_content=cache_name)
        else:
            config = types.GenerateContentConfig(system_instruction=system)

        stream = await self.client.aio.models.generate_content_stream(
            model=self.model, contents=contents, config=config
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text
