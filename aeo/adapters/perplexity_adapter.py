"""Perplexity adapter — Agent API.

Perplexity retired the Sonar chat-completions endpoint on 27 September 2026.
Sonar's quality tiers (sonar, sonar-pro, sonar-reasoning-pro) became *presets*
on a single model. The Agent API's canonical path is /v1/agent, and
/v1/responses is kept as an OpenAI-compatible alias, which is what lets us
keep using the OpenAI SDK here instead of adding another dependency.

Model IDs and request shapes on this provider have moved twice in a year, so
this tries a short list of candidates and remembers the first that works
rather than hardcoding one that will break again. If all of them fail, the
error names every attempt so the next fix is obvious.

Perplexity is the one provider where grounding cannot accidentally be off:
searching is what the model does.
"""
from __future__ import annotations

import os

from .base import Adapter, EngineResult, build_citations, jsonable

# The OpenAI SDK appends the path to base_url, so base_url must include /v1
# for responses.create() to reach /v1/responses. Without it the SDK posts to
# /responses and every call 404s. Both spellings are tried in case the alias
# moves again.
BASE_URLS = ["https://api.perplexity.ai/v1", "https://api.perplexity.ai"]

# The bare "sonar" id is rejected on the Agent API; models are namespaced now.
MODEL_CANDIDATES = ["perplexity/sonar", "sonar-pro", "sonar"]

# CRITICAL: the Agent API does NOT search unless you pass the tool. Old Sonar
# always searched, so a straight port returns a fluent answer with an empty
# `annotations` array and no error — an ungrounded answer that looks like
# data. This is the single easiest way to poison a study.
TOOLS = [{"type": "web_search"}]

# Perplexity's own docs pair the tool with an instruction, because the model
# otherwise asks permission instead of searching. Kept deliberately minimal
# and neutral: it tells the model to search, not what to conclude. Any study
# using this must disclose it, since the other three engines get no equivalent
# nudge.
INSTRUCTIONS = ("You have access to a web_search tool. Search before "
                "answering. Never ask permission to search.")


def _harvest(resp) -> list[tuple[str, str]]:
    """Pull citations out of whichever shape this response arrived in.

    Perplexity has returned sources as `citations`, `search_results`, and as
    url_citation annotations on output blocks at different times. Checking all
    three costs nothing and survives the next change.
    """
    found: list[tuple[str, str]] = []

    def add(u, t=""):
        if isinstance(u, str) and u.strip():
            found.append((u, t or ""))

    for item in (getattr(resp, "citations", None) or []):
        add(item) if isinstance(item, str) else add(
            (item or {}).get("url"), (item or {}).get("title"))

    for sr in (getattr(resp, "search_results", None) or []):
        if isinstance(sr, dict):
            add(sr.get("url"), sr.get("title"))
        else:
            add(getattr(sr, "url", ""), getattr(sr, "title", ""))

    for item in (getattr(resp, "output", None) or []):
        for block in (getattr(item, "content", None) or []):
            for ann in (getattr(block, "annotations", None) or []):
                add(getattr(ann, "url", None), getattr(ann, "title", "") or "")

    # Last resort: walk the serialized payload for anything url-shaped.
    if not found:
        def walk(o):
            if isinstance(o, dict):
                u = o.get("url")
                if isinstance(u, str) and u.startswith("http"):
                    add(u, o.get("title", "") or "")
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(jsonable(resp))
    return found


class PerplexityAdapter(Adapter):
    name = "perplexity"
    env_key = "PERPLEXITY_API_KEY"
    model = MODEL_CANDIDATES[0]

    _working: tuple[str, str] | None = None      # (base_url, model_id)

    def run(self, prompt: str) -> EngineResult:
        if not self.available():
            return EngineResult(self.name, ok=False,
                                error="PERPLEXITY_API_KEY not set")
        try:
            from openai import OpenAI      # /v1/responses is OpenAI-compatible
        except ImportError:
            return EngineResult(self.name, ok=False,
                                error="pip install openai")

        key = os.environ["PERPLEXITY_API_KEY"]
        pairs = ([PerplexityAdapter._working] if PerplexityAdapter._working
                 else [(b, m) for b in BASE_URLS for m in MODEL_CANDIDATES])
        errors: list[str] = []

        for base, model_id in pairs:
            try:
                client = OpenAI(api_key=key, base_url=base)
                resp = client.responses.create(
                    model=model_id, input=prompt,
                    tools=TOOLS, instructions=INSTRUCTIONS)
                text = getattr(resp, "output_text", "") or ""
                if not text:                       # assemble it from blocks
                    parts = []
                    for item in (getattr(resp, "output", None) or []):
                        for block in (getattr(item, "content", None) or []):
                            parts.append(getattr(block, "text", "") or "")
                    text = "".join(parts)
                if not text.strip():
                    errors.append(f"{base} {model_id}: empty response")
                    continue
                PerplexityAdapter._working = (base, model_id)
                return EngineResult(
                    engine=self.name,
                    answer_text=text,
                    citations=build_citations(_harvest(resp)),
                    raw=jsonable(resp),
                    model_version=getattr(resp, "model", model_id) or model_id,
                )
            except Exception as e:
                errors.append(f"{base.rsplit('/', 1)[-1]}/{model_id}: "
                              f"{str(e)[:110]}")

        return EngineResult(self.name, ok=False,
                            error="all models failed — " + " | ".join(errors))
