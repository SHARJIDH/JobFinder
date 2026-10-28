"""Model-provider abstraction with structured output, caching, budgets and usage logging."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..core.text import sha256_text
from ..models import LlmCache, LlmUsage

T = TypeVar("T", bound=BaseModel)
Tier = Literal["fast", "quality"]


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    pass


class BudgetExceeded(LLMError):
    pass


class ProviderResponse(BaseModel):
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str


class Provider(Protocol):
    name: str

    def model_for(self, tier: Tier) -> str: ...

    def generate(self, *, system: str, prompt: str, schema: type[BaseModel], tier: Tier,
                 max_output_tokens: int) -> ProviderResponse: ...


class GeminiVertexProvider:
    """Gemini via Vertex AI using the google-genai SDK and Application Default Credentials."""

    name = "gemini-vertex"

    def __init__(self, settings: Settings):
        if not settings.google_cloud_project:
            raise LLMUnavailable("GOOGLE_CLOUD_PROJECT is not set")
        from google import genai  # imported lazily so tests/offline mode don't need it

        self.settings = settings
        self.client = genai.Client(vertexai=True, project=settings.google_cloud_project,
                                   location=settings.google_cloud_location)

    def model_for(self, tier: Tier) -> str:
        return self.settings.gemini_model_quality if tier == "quality" else self.settings.gemini_model_fast

    def generate(self, *, system, prompt, schema, tier, max_output_tokens, images: list[bytes] | None = None) -> ProviderResponse:
        from google.genai import types

        model = self.model_for(tier)
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            response_schema=schema,
            http_options=types.HttpOptions(timeout=self.settings.llm_timeout_seconds * 1000),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if "2.5" in model:
            # Thinking tokens are billed; structured extraction does not need them.
            cfg.thinking_config = types.ThinkingConfig(thinking_budget=0)
        try:
            contents = ([types.Part.from_bytes(data=img, mime_type="image/png") for img in images] + [prompt]) if images else prompt
            r = self.client.models.generate_content(model=model, contents=contents, config=cfg)
        except Exception as e:  # SDK raises several error types
            raise LLMError(f"Gemini call failed: {type(e).__name__}: {str(e)[:300]}") from e
        u = r.usage_metadata
        out_tokens = (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
        return ProviderResponse(text=r.text or "", input_tokens=getattr(u, "prompt_token_count", 0) or 0,
                                output_tokens=out_tokens, model=model)

    def grounded_generate(self, *, prompt: str, max_output_tokens: int) -> tuple[ProviderResponse, list[dict], list[dict]]:
        """Gemini + Grounding with Google Search. Returns text, cited web sources (by index) and
        grounding supports (which text segment is backed by which sources)."""
        from google.genai import types

        model = self.settings.gemini_model_search
        cfg = types.GenerateContentConfig(
            temperature=0, max_output_tokens=max_output_tokens,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            http_options=types.HttpOptions(timeout=self.settings.llm_timeout_seconds * 1000),
        )
        if "2.5" in model:
            cfg.thinking_config = types.ThinkingConfig(thinking_budget=0)  # thinking tokens are billed and not needed
        try:
            r = self.client.models.generate_content(model=model, contents=prompt, config=cfg)
        except Exception as e:
            raise LLMError(f"Grounded search failed: {type(e).__name__}: {str(e)[:300]}") from e
        u = r.usage_metadata
        out_tokens = (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
        gm = r.candidates[0].grounding_metadata if r.candidates else None
        sources = [{"uri": c.web.uri if getattr(c, "web", None) else "", "title": (c.web.title or "") if getattr(c, "web", None) else ""}
                   for c in (getattr(gm, "grounding_chunks", None) or [])]
        supports = [{"text": (s.segment.text or "") if s.segment else "", "chunks": list(s.grounding_chunk_indices or [])}
                    for s in (getattr(gm, "grounding_supports", None) or [])]
        return (ProviderResponse(text=r.text or "", input_tokens=getattr(u, "prompt_token_count", 0) or 0,
                                 output_tokens=out_tokens, model=model), sources, supports)


def build_provider(settings: Settings | None = None) -> Provider | None:
    settings = settings or get_settings()
    if settings.llm_provider.lower() in ("gemini", "vertex", "gemini-vertex"):
        try:
            return GeminiVertexProvider(settings)
        except Exception:
            return None
    return None


def _extract_json(text: str) -> str:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        return m.group(1)
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    return t[start:] if start > 0 else t


class LLMService:
    def __init__(self, db: Session, provider: Provider | None = None, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.provider = provider if provider is not None else build_provider(self.settings)

    @property
    def available(self) -> bool:
        return self.provider is not None

    # ---- budgets -------------------------------------------------------
    def _today_usage(self) -> tuple[int, float]:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        row = self.db.execute(
            select(func.count(LlmUsage.id), func.coalesce(func.sum(LlmUsage.est_cost_usd), 0.0))
            .where(LlmUsage.created_at >= start, LlmUsage.cached.is_(False))
        ).one()
        return int(row[0]), float(row[1])

    def budget_status(self) -> dict:
        calls, cost = self._today_usage()
        return {
            "calls_today": calls, "call_limit": self.settings.llm_daily_call_limit,
            "est_cost_today_usd": round(cost, 4), "budget_usd": self.settings.llm_daily_budget_usd,
            "provider": self.provider.name if self.provider else "disabled",
        }

    def _check_budget(self) -> None:
        calls, cost = self._today_usage()
        if calls >= self.settings.llm_daily_call_limit:
            raise BudgetExceeded(f"Daily LLM call limit reached ({calls}/{self.settings.llm_daily_call_limit})")
        if cost >= self.settings.llm_daily_budget_usd:
            raise BudgetExceeded(f"Daily LLM budget reached (${cost:.3f} of ${self.settings.llm_daily_budget_usd:.2f})")

    def _cost(self, tier: Tier, inp: int, out: int) -> float:
        s = self.settings
        if tier == "quality":
            return inp / 1e6 * s.price_quality_input_per_m + out / 1e6 * s.price_quality_output_per_m
        return inp / 1e6 * s.price_fast_input_per_m + out / 1e6 * s.price_fast_output_per_m

    # ---- main entry ----------------------------------------------------
    def structured(self, *, task: str, system: str, prompt: str, schema: type[T], tier: Tier = "fast",
                   prompt_version: str = "v1", max_output_tokens: int = 2048, use_cache: bool = True,
                   images: list[bytes] | None = None) -> tuple[T, dict]:
        if not self.provider:
            raise LLMUnavailable("No LLM provider configured (set LLM_PROVIDER=gemini and GOOGLE_CLOUD_PROJECT)")
        model = self.provider.model_for(tier)
        key = sha256_text(task, prompt_version, model, system, prompt, json.dumps(schema.model_json_schema(), sort_keys=True),
                          *(hashlib.sha256(i).hexdigest() for i in images or []))
        if use_cache:
            hit = self.db.get(LlmCache, key)
            if hit:
                try:
                    obj = schema.model_validate_json(hit.response_json)
                    self.db.add(LlmUsage(task=task, model=model, cached=True))
                    self.db.commit()
                    return obj, {"model": model, "cached": True, "est_cost_usd": 0.0}
                except ValidationError:
                    self.db.delete(hit)
        self._check_budget()
        last_err = ""
        cur_prompt = prompt
        for attempt in range(2):
            try:
                kw = {"images": images} if images else {}
                resp = self.provider.generate(system=system, prompt=cur_prompt, schema=schema, tier=tier,
                                              max_output_tokens=max_output_tokens, **kw)
            except LLMError as e:
                self.db.add(LlmUsage(task=task, model=model, success=False, error=str(e)[:1000]))
                self.db.commit()
                raise
            cost = self._cost(tier, resp.input_tokens, resp.output_tokens)
            try:
                obj = schema.model_validate_json(_extract_json(resp.text))
            except ValidationError as e:
                last_err = str(e)[:500]
                self.db.add(LlmUsage(task=task, model=resp.model, input_tokens=resp.input_tokens,
                                     output_tokens=resp.output_tokens, est_cost_usd=cost, success=False,
                                     error=f"schema validation failed: {last_err}"))
                self.db.commit()
                cur_prompt = prompt + "\n\nYour previous reply was not valid JSON for the schema. Reply with JSON only."
                continue
            self.db.add(LlmUsage(task=task, model=resp.model, input_tokens=resp.input_tokens,
                                 output_tokens=resp.output_tokens, est_cost_usd=cost))
            if use_cache:
                self.db.merge(LlmCache(key=key, task=task, model=resp.model, response_json=obj.model_dump_json()))
            self.db.commit()
            return obj, {"model": resp.model, "cached": False, "est_cost_usd": round(cost, 6)}
        raise LLMError(f"Model output failed schema validation twice: {last_err}")

    def grounded_search(self, *, task: str, prompt: str, max_output_tokens: int = 2048) -> tuple[str, list[dict], list[dict], dict]:
        """Web search through Gemini grounding. Cached per UTC day; counted against the same daily caps."""
        if not self.provider or not hasattr(self.provider, "grounded_generate"):
            raise LLMUnavailable("Grounded web search needs LLM_PROVIDER=gemini")
        day = datetime.now(timezone.utc).date().isoformat()
        model = getattr(self.settings, "gemini_model_search", "search")
        key = sha256_text(task, "grounded-v2", model, day, prompt)
        hit = self.db.get(LlmCache, key)
        if hit:
            data = json.loads(hit.response_json)
            self.db.add(LlmUsage(task=task, model=model, cached=True))
            self.db.commit()
            return data["text"], data["sources"], data["supports"], {"model": model, "cached": True, "est_cost_usd": 0.0}
        self._check_budget()
        try:
            resp, sources, supports = self.provider.grounded_generate(prompt=prompt, max_output_tokens=max_output_tokens)
        except LLMError as e:
            self.db.add(LlmUsage(task=task, model=model, success=False, error=str(e)[:1000]))
            self.db.commit()
            raise
        cost = self._cost("quality", resp.input_tokens, resp.output_tokens) + self.settings.price_grounded_prompt_usd
        self.db.add(LlmUsage(task=task, model=resp.model, input_tokens=resp.input_tokens,
                             output_tokens=resp.output_tokens, est_cost_usd=cost))
        self.db.merge(LlmCache(key=key, task=task, model=resp.model,
                               response_json=json.dumps({"text": resp.text, "sources": sources, "supports": supports})))
        self.db.commit()
        return resp.text, sources, supports, {"model": resp.model, "cached": False, "est_cost_usd": round(cost, 6)}
