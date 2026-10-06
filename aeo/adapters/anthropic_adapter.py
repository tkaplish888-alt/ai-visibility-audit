"""Anthropic (Claude) adapter. Messages API with the web_search server tool so
citations are captured from grounded answers.

WHAT CHANGED (study extension):
  * max_tokens 1024 -> 4096. This was NOT a cost decision dressed up as a
    quality one: you are billed for tokens the model actually generates, not
    for the ceiling you allow. At 1024, 52% of the existing 1,022 answers
    stopped mid-sentence, which systematically undercounts brands named late
    and corrupts first-position metrics. Raising the ceiling costs nothing on
    answers that finish early.
  * max_uses 2 -> 3. This one DOES cost money (each search is billed, and the
    retrieved results enter the context as input tokens), so it is a deliberate
    trade: enough grounding depth for a comparison question, held identical
    across providers so the cross-model comparison stays fair.
  * Full URLs and titles are returned, plus the raw payload.
"""
from __future__ import annotations

from .base import Adapter, EngineResult, build_citations, jsonable

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096
MAX_SEARCHES = 3


class AnthropicAdapter(Adapter):
    name = "anthropic"
    env_key = "ANTHROPIC_API_KEY"
    model = MODEL

    def run(self, prompt: str) -> EngineResult:
        if not self.available():
            return EngineResult(self.name, ok=False, error="ANTHROPIC_API_KEY not set")
        try:
            import anthropic

            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                tools=[{"type": "web_search_20250305", "name": "web_search",
                        "max_uses": MAX_SEARCHES}],
            )
            text_parts: list[str] = []
            found: list[tuple[str, str]] = []
            for block in msg.content:
                btype = getattr(block, "type", "")
                if btype == "text":
                    text_parts.append(getattr(block, "text", ""))
                    for c in getattr(block, "citations", []) or []:
                        url = getattr(c, "url", None)
                        if url:
                            found.append((url, getattr(c, "title", "") or ""))
                elif btype == "web_search_tool_result":
                    for r in getattr(block, "content", []) or []:
                        url = getattr(r, "url", None)
                        if url:
                            found.append((url, getattr(r, "title", "") or ""))
            return EngineResult(
                engine=self.name,
                answer_text="".join(text_parts),
                citations=build_citations(found),
                raw=jsonable(msg),
                model_version=getattr(msg, "model", MODEL) or MODEL,
            )
        except Exception as e:
            return EngineResult(self.name, ok=False, error=str(e))
