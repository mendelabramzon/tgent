from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError

from app.models import ReplySuggestion

logger = logging.getLogger(__name__)


class OpenAIClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        max_retries: int,
    ):
        self.enabled = bool(api_key and api_key.strip())
        self.model = model
        self.max_retries = max(0, int(max_retries))
        self._client: AsyncOpenAI | None = None

        if self.enabled:
            self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)

    async def suggest_reply(self, *, system_prompt: str, user_prompt: str) -> ReplySuggestion:
        if not self.enabled or self._client is None:
            raise RuntimeError("OpenAI is not configured (missing OPENAI_API_KEY).")

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self._suggest_once(system_prompt=system_prompt, user_prompt=user_prompt)
            except (RateLimitError, APITimeoutError, APIError, json.JSONDecodeError, ValueError) as e:
                last_err = e
                delay = min(8.0, 2.0**attempt)
                logger.warning(
                    "OpenAI call failed (attempt %s/%s): %s. Retrying in %.1fs",
                    attempt + 1,
                    self.max_retries + 1,
                    type(e).__name__,
                    delay,
                )
                await asyncio.sleep(delay)

        assert last_err is not None
        raise last_err

    async def _suggest_once(self, *, system_prompt: str, user_prompt: str) -> ReplySuggestion:
        assert self._client is not None
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=600,
        )

        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("Empty OpenAI response content")

        data: dict[str, Any] = json.loads(content)
        return ReplySuggestion.model_validate(data)


