from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import APIError, APITimeoutError, AsyncOpenAI, BadRequestError, RateLimitError

from app.models import ReplySuggestion

logger = logging.getLogger(__name__)

_DEFAULT_MAX_OUTPUT_TOKENS = 600
# GPT-5 models can spend a meaningful portion of the completion budget on internal reasoning.
# If the budget is too low, you may receive a 200 OK with empty visible content.
_GPT5_MAX_OUTPUT_TOKENS = 2000
_DEFAULT_TEMPERATURE = 0.7


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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        # Some newer model families restrict sampling params (e.g. GPT-5 only supports default temperature=1).
        if not self.model.startswith("gpt-5"):
            kwargs["temperature"] = _DEFAULT_TEMPERATURE

        # Token parameter compatibility:
        # Some models (e.g. GPT-5) require `max_completion_tokens` instead of `max_tokens`.
        # We'll auto-detect once and then stick with it.
        token_param: str | None = getattr(self, "_token_param", None)
        if token_param is None:
            token_param = "max_completion_tokens" if self.model.startswith("gpt-5") else "max_tokens"
            setattr(self, "_token_param", token_param)

        max_out = _GPT5_MAX_OUTPUT_TOKENS if self.model.startswith("gpt-5") else _DEFAULT_MAX_OUTPUT_TOKENS
        kwargs[token_param] = max_out

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except BadRequestError as e:
            msg = str(e)
            # Automatic fallback if the chosen token parameter is not supported.
            if "max_tokens" in msg and "max_completion_tokens" in msg:
                if "max_tokens' is not supported" in msg or "Use 'max_completion_tokens' instead" in msg:
                    setattr(self, "_token_param", "max_completion_tokens")
                    kwargs.pop("max_tokens", None)
                    kwargs["max_completion_tokens"] = _DEFAULT_MAX_OUTPUT_TOKENS
                    resp = await self._client.chat.completions.create(**kwargs)
                elif "max_completion_tokens' is not supported" in msg or "Use 'max_tokens' instead" in msg:
                    setattr(self, "_token_param", "max_tokens")
                    kwargs.pop("max_completion_tokens", None)
                    kwargs["max_tokens"] = _DEFAULT_MAX_OUTPUT_TOKENS
                    resp = await self._client.chat.completions.create(**kwargs)
                else:
                    raise
            elif "temperature" in msg and ("Only the default" in msg or "unsupported" in msg):
                # Some models only support default temperature; omit it and retry once.
                kwargs.pop("temperature", None)
                resp = await self._client.chat.completions.create(**kwargs)
            else:
                raise

        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("Empty OpenAI response content")

        data: dict[str, Any] = json.loads(content)
        return ReplySuggestion.model_validate(data)


