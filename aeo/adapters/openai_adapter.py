"""OpenAI (ChatGPT) adapter. Responses API with the web_search tool so
citations are captured. Query the model tier a real user hits, not the cheapest.

Provider SDKs change. The only contract the rest of the system depends on is
the returned EngineResult, so if a call shape breaks, fix it inside this file.
"""
from __future__ import annotations

from .base import Adapter, EngineResult, build_citations, jsonable

MODEL = "gpt-5"          # match the tier real users get
MAX_SEARCHES = 3         # held equal across providers for a fair comparison


class OpenAIAdapter(Adapter):
    name = "openai"
    env_key = "OPENAI_API_KEY"
    model = MODEL

    def run(self, prompt: str) -> EngineResult:
        if not self.available():
            return EngineResult(self.name, ok=False, error="OPENAI_API_KEY not set")
        try:
            from openai import OpenAI

            client = OpenAI()
            resp = client.responses.create(
                model=MODEL,
                input=prompt,
                tools=[{"type": "web_search"}],
            )
            text = getattr(resp, "output_text", "") or ""
            found: list[tuple[str, str]] = []
            # Citations arrive as url_citation annotations on output text blocks.
            for item in getattr(resp, "output", []) or []:
                for block in getattr(item, "content", []) or []:
                    for ann in getattr(block, "annotations", []) or []:
                        url = getattr(ann, "url", None)
                        if url:
                            found.append((url, getattr(ann, "title", "") or ""))
            return EngineResult(
                engine=self.name,
                answer_text=text,
                citations=build_citations(found),
                raw=jsonable(resp),
                model_version=getattr(resp, "model", MODEL) or MODEL,
            )
        except Exception as e:                       # graceful failure
            return EngineResult(self.name, ok=False, error=str(e))
