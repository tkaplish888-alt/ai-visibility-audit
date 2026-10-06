"""Gemini adapter — generateContent with Google Search grounding.

Two things to know about this provider.

First, model IDs churn fast and old ones are closed to NEW keys while still
working for existing ones, so a name that works in one account 404s in
another. This tries a list newest-first and caches whichever your key can
actually reach. Run `python3 -m aeo.cli models` to see the real list for your
key.

Second, and more dangerous: Google's free tier has not included Search
grounding. An ungrounded key returns a fluent, confident, completely
unsourced answer and NO error. That looks like data and is not. Always run
the smoke test and confirm citations are above zero before trusting a run.

Gemini also returns grounding sources as redirect URLs through Google's own
domain, so the publisher's real domain is read from the chunk's `domain` and
`title` fields where present. Without that, every Gemini citation would come
out as vertexaisearch.cloud.google.com and tell you nothing.
"""
from __future__ import annotations

from .base import Adapter, EngineResult, build_citations, jsonable

# Newest first. gemini-2.5-* are kept as a tail for older keys but are closed
# to new ones, so they will usually 404 and fall through harmlessly.
MODEL_CANDIDATES = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]


class GeminiAdapter(Adapter):
    name = "gemini"
    env_key = "GEMINI_API_KEY"
    model = MODEL_CANDIDATES[0]

    _working_model: str | None = None

    def run(self, prompt: str) -> EngineResult:
        if not self.available():
            return EngineResult(self.name, ok=False,
                                error="GEMINI_API_KEY not set")
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return EngineResult(self.name, ok=False,
                                error="pip install google-genai")

        client = genai.Client()
        # Gemini decides for itself whether to search, and in the first study
        # run it declined on 67 of 90 answers — 74% ungrounded, with no error.
        # A system instruction pushes it to actually use the tool. Perplexity
        # needed the same nudge. Disclose both in any study's methodology:
        # two of four engines were instructed to search and two were not.
        cfg = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            system_instruction=(
                "Use the google_search tool to ground your answer in current "
                "web sources before responding. Do not answer from memory "
                "alone. Never ask permission to search."),
        )
        candidates = ([GeminiAdapter._working_model]
                      if GeminiAdapter._working_model else MODEL_CANDIDATES)
        errors: list[str] = []

        for model_id in candidates:
            try:
                resp = client.models.generate_content(
                    model=model_id, contents=prompt, config=cfg)
                text = getattr(resp, "text", "") or ""
                if not text.strip():
                    errors.append(f"{model_id}: empty response")
                    continue

                found: list[tuple[str, str]] = []
                for cand in (getattr(resp, "candidates", None) or []):
                    meta = getattr(cand, "grounding_metadata", None)
                    for chunk in (getattr(meta, "grounding_chunks", None) or []):
                        web = getattr(chunk, "web", None)
                        uri = getattr(web, "uri", None)
                        if not uri:
                            continue
                        # `uri` is a Google redirect through
                        # vertexaisearch.cloud.google.com, which is useless as
                        # a source. The real publisher lives in `domain`, and
                        # when that is empty Gemini usually puts the bare
                        # domain in `title` instead. Falling back to the
                        # redirect, as the first version did, made 100% of
                        # Gemini's citations resolve to Google.
                        title = getattr(web, "title", "") or ""
                        real = getattr(web, "domain", "") or ""
                        if not real and "." in title and " " not in title.strip():
                            real = title.strip()
                        if not real:
                            continue          # unresolvable: drop, never log Google
                        found.append((real, title))

                GeminiAdapter._working_model = model_id
                return EngineResult(
                    engine=self.name,
                    answer_text=text,
                    citations=build_citations(found),
                    raw=jsonable(resp),
                    model_version=model_id,
                )
            except Exception as e:
                errors.append(f"{model_id}: {str(e)[:150]}")

        return EngineResult(self.name, ok=False,
                            error="all models failed — " + " | ".join(errors))
