"""
Gemini NLU client for the real-time pipeline.

One bounded JSON request per Tier-1 turn. Everything that can stall a live call
is capped here: a hard deadline per turn, at most one retry (only if enough
budget is left), and a circuit breaker so a key that is rate-limited or erroring
is skipped instead of retried on every turn. A failed startup check is retried
in the background (keep_verified) instead of disabling Gemini for good.

generate_json serves the old 12-step engine; generate_json_stream is the R2
engine's one streamed request per turn (nlu.py reads it as it arrives).

The model only extracts and phrases. It never decides a state transition — see
ai_engine._handle_conversation_step — so any failure here degrades to the
deterministic fallback rather than to a wrong action.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date

import clock
import config

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    GENAI_AVAILABLE = False

# Cool-down after a key errors (429 quota, 5xx overload, auth...).
KEY_COOLDOWN_S = 30.0
# A retry is only worth it when this much of the turn budget is still left.
MIN_RETRY_BUDGET_S = 0.7

_NULL_STRINGS = {"null", "none", "", "undefined", "n/a"}


def parse_json_object(text: str) -> dict:
    """Parse a model's JSON reply, tolerating code fences; {} when unusable."""
    if not text:
        return {}
    clean = re.sub(r"^```(?:json)?\s*|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        # Salvage the outermost object if the model wrapped it in prose.
        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return {}
    if not isinstance(data, dict):
        return {}
    for key, value in list(data.items()):
        if isinstance(value, str) and value.strip().lower() in _NULL_STRINGS:
            data[key] = None
    return data


@dataclass
class _Key:
    alias: str
    value: str
    client: object = None
    cooldown_until: float = 0.0
    day: date = field(default_factory=clock.today)
    requests: int = 0
    failures: int = 0

    def count(self, failed: bool = False):
        today = clock.today()
        if today != self.day:
            self.day, self.requests, self.failures = today, 0, 0
        self.requests += 1
        if failed:
            self.failures += 1


def _thinking_config(value: str):
    if not value or not GENAI_AVAILABLE:
        return None
    if value.lstrip("-").isdigit():
        return genai_types.ThinkingConfig(thinking_budget=int(value))
    return genai_types.ThinkingConfig(thinking_level=value.upper())


class GeminiNLU:
    def __init__(self, keys=None, model=None, timeout=None, thinking=None):
        if keys is None:
            keys = [config.GEMINI_API_KEY] if config.GEMINI_API_KEY else []
        self.keys = [_Key(alias=f"key{i + 1}", value=k) for i, k in enumerate(keys)]
        self.model = config.GEMINI_MODEL if model is None else model
        self.timeout = config.GEMINI_TIMEOUT if timeout is None else timeout
        self.thinking = config.GEMINI_THINKING if thinking is None else thinking
        # Used for the one retry after an overload/5xx on the primary model.
        self.fallback_model = config.GEMINI_FALLBACK_MODEL
        # None = not verified yet (library use: assume usable); False = the
        # startup check failed, so every turn goes straight to the fallback.
        self.available: bool | None = None
        self._next = 0

    # -- key pool ------------------------------------------------------------
    def _client(self, key: _Key):
        if key.client is None:
            key.client = genai.Client(api_key=key.value)
        return key.client

    def _pick(self, exclude=()):
        now = time.monotonic()
        for offset in range(len(self.keys)):
            key = self.keys[(self._next + offset) % len(self.keys)]
            if key in exclude or key.cooldown_until > now:
                continue
            self._next = (self.keys.index(key) + 1) % len(self.keys)
            return key
        return None

    @property
    def usable(self) -> bool:
        return GENAI_AVAILABLE and bool(self.keys) and bool(self.model) and self.available is not False

    def _config(self, system: str, max_tokens: int, json_mode: bool, schema: dict | None = None):
        cfg = {
            "system_instruction": system,
            "temperature": 0.0,
            "max_output_tokens": max_tokens,
            "automatic_function_calling": genai_types.AutomaticFunctionCallingConfig(disable=True),
        }
        if json_mode:
            cfg["response_mime_type"] = "application/json"
        if schema:
            cfg["response_schema"] = _schema(schema)
        thinking = _thinking_config(self.thinking)
        if thinking is not None:
            cfg["thinking_config"] = thinking
        return genai_types.GenerateContentConfig(**cfg)

    # -- calls ---------------------------------------------------------------
    async def generate_json(self, contents: str, system: str, max_tokens: int = 200) -> dict | None:
        """One JSON request within the turn budget. None means "use the fallback"."""
        if not self.usable:
            return None
        deadline = time.monotonic() + self.timeout
        tried = []
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if attempt and remaining < MIN_RETRY_BUDGET_S:
                break
            # Prefer a different key for the retry, but a single-key setup may
            # retry on the same one: overload errors are transient.
            key = self._pick(exclude=tried) or (self._pick() if attempt else None)
            if key is None:
                break
            tried.append(key)
            model = self.model if attempt == 0 else (self.fallback_model or self.model)
            try:
                response = await asyncio.wait_for(
                    self._client(key).aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=self._config(system, max_tokens, json_mode=True),
                    ),
                    timeout=remaining,
                )
                key.count()
                return parse_json_object(response.text or "")
            except asyncio.TimeoutError:
                key.count(failed=True)
                logger.warning("Gemini NLU timed out on %s (%s) after %.1fs", key.alias, model, self.timeout)
                break  # the budget is spent; a retry cannot finish in time
            except Exception as exc:  # quota, overload, auth, network
                key.count(failed=True)
                if _is_key_problem(exc):
                    # Quota or auth: this key will keep failing, so skip it for a while.
                    key.cooldown_until = time.monotonic() + KEY_COOLDOWN_S
                logger.warning("Gemini NLU failed on %s (%s): %s", key.alias, model, _short(exc))
        return None

    async def generate_json_stream(self, contents: str, system: str, *, schema: dict | None = None,
                                   deadline: float | None = None, max_tokens: int = 400):
        """
        The R2 turn's one request, streamed (docs/R2_DESIGN.md, sections 5
        and 14): an async iterator of raw text chunks of a JSON object that
        follows `schema` (nlu.build_schema). Nothing yielded means "use the
        fallback".

        `deadline` is an absolute time.monotonic() value (default: now +
        GEMINI_TIMEOUT). Same key, breaker and cool-down rules as
        generate_json, but the retry rule is stricter, because the caller may
        already be hearing the first sentence: at most one retry, only before
        the first token arrived and only with MIN_RETRY_BUDGET_S left. Once a
        token has been yielded there is never a second call; an error after
        that just ends the stream and the engine falls back for whatever is
        missing. Closing the iterator (aclose) closes the HTTP stream.
        """
        if not self.usable:
            return
        if deadline is None:
            deadline = time.monotonic() + self.timeout
        tried = []
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (attempt and remaining < MIN_RETRY_BUDGET_S):
                break
            key = self._pick(exclude=tried) or (self._pick() if attempt else None)
            if key is None:
                break
            tried.append(key)
            model = self.model if attempt == 0 else (self.fallback_model or self.model)
            yielded = False
            iterator = None
            try:
                stream = await asyncio.wait_for(
                    self._client(key).aio.models.generate_content_stream(
                        model=model,
                        contents=contents,
                        config=self._config(system, max_tokens, json_mode=True, schema=schema),
                    ),
                    timeout=remaining,
                )
                iterator = stream.__aiter__()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    try:
                        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                    except StopAsyncIteration:
                        break
                    text = _chunk_text(chunk)
                    if text:
                        yielded = True
                        yield text
                key.count()
                return
            except asyncio.TimeoutError:
                key.count(failed=True)
                logger.warning("Gemini NLU stream timed out on %s (%s)%s", key.alias, model,
                               " mid-reply" if yielded else "")
                return  # the budget is spent; a retry cannot finish in time
            except Exception as exc:  # quota, overload, auth, network
                key.count(failed=True)
                if _is_key_problem(exc):
                    key.cooldown_until = time.monotonic() + KEY_COOLDOWN_S
                logger.warning("Gemini NLU stream failed on %s (%s)%s: %s", key.alias, model,
                               " mid-reply" if yielded else "", _short(exc))
                if yielded:
                    return  # tokens already reached the caller: never a second call
            finally:
                if iterator is not None and hasattr(iterator, "aclose"):
                    try:
                        await iterator.aclose()
                    except Exception:
                        pass

    async def verify_model(self, timeout: float = 10.0, quiet: bool = False) -> bool:
        """Health check: the configured model must answer a tiny request."""
        if not GENAI_AVAILABLE or not self.keys or not self.model:
            self.available = False
            logger.warning("Gemini disabled: %s", "no API key" if not self.keys else "no GEMINI_MODEL")
            return False
        key = self.keys[0]
        was_down = self.available is False
        try:
            await asyncio.wait_for(
                self._client(key).aio.models.generate_content(
                    model=self.model,
                    contents="Reply with {\"ok\": true}",
                    config=self._config("Reply with JSON only.", 16, json_mode=True),
                ),
                timeout=timeout,
            )
            self.available = True
            logger.info("Gemini model %s: %s", "recovered" if was_down else "verified", self.model)
            return True
        except Exception as exc:
            if not _definitive(exc):
                # Slow or busy (a timeout, 429, 5xx): the model is probably fine. Keep
                # using it and check again soon, rather than putting every call on
                # the fallback until the next re-check (6 Oct: a slow startup check
                # switched Gemini off for a minute).
                self.available = None
                logger.warning("Gemini model %r check was slow or busy (%s); still using it, checking again",
                               self.model, _short(exc))
                return False
            self.available = False
            if quiet:
                logger.warning("Gemini model %r still failing its check: %s", self.model, _short(exc))
            else:
                logger.error(
                    "Gemini model %r failed its startup check (%s). Calls will run on "
                    "Tier-0 + templates until it recovers. Available Flash-Lite models: %s",
                    self.model, _short(exc), await self._flash_lite_models(key),
                )
            return False

    async def keep_verified(self, interval: float | None = None):
        """
        Background task: while the model check is failing, re-run it every
        `interval` seconds. A quota or network blip at startup would otherwise
        leave every call on the fallback path until the server restarts.
        Does nothing when Gemini is simply not configured.
        """
        interval = config.GEMINI_REVERIFY_S if interval is None else interval
        if not GENAI_AVAILABLE or not self.keys or not self.model:
            return
        while True:
            await asyncio.sleep(interval if self.available is False else min(interval, 15))
            if self.available is not True:
                await self.verify_model(quiet=True)

    async def _flash_lite_models(self, key) -> str:
        try:
            pager = await self._client(key).aio.models.list()
            names = [m.name async for m in pager if "flash-lite" in (m.name or "")]
            return ", ".join(sorted(names)) or "none"
        except Exception:
            return "unknown"

    def status(self) -> dict:
        now = time.monotonic()
        return {
            "model": self.model,
            "verified": self.available,
            "keys": [
                {
                    "alias": k.alias,
                    "requests_today": k.requests,
                    "failures_today": k.failures,
                    "cooling_down": k.cooldown_until > now,
                }
                for k in self.keys
            ],
        }


def _schema(schema: dict):
    """The response schema as google-genai's Schema (validated up front); the dict if that fails."""
    try:
        return genai_types.Schema.model_validate(schema)
    except Exception:
        return schema


def _chunk_text(chunk) -> str:
    """The text of one streamed response chunk ("" for thought-only or empty chunks)."""
    try:
        return chunk.text or ""
    except Exception:
        return ""


def _is_key_problem(exc: Exception) -> bool:
    """429 quota / 401-403 auth errors are about the key; 5xx overload is not."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        code = int(code)
    except (TypeError, ValueError):
        return False
    return code in (401, 403, 429)


def _definitive(exc: Exception) -> bool:
    """A failure that retrying won't fix soon: unknown model, bad or unauthorised key."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return False
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (400, 401, 403, 404):
        return True
    text = str(exc).lower()
    return any(w in text for w in ("not found", "permission", "api key", "api_key", "unauthenticated",
                                   "invalid argument"))


def _short(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:160]}"


_nlu: GeminiNLU | None = None


def get_nlu() -> GeminiNLU:
    """Process-wide client (connection reuse); per-call state never lives here."""
    global _nlu
    if _nlu is None:
        _nlu = GeminiNLU()
    return _nlu
