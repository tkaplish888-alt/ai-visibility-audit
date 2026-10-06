"""Stage 4 — Parsing. For each response, extract:

  * Mentions   — alias hits with word-boundary matching ("Notion" != "notionally"),
                 recording count and first position (for prominence).
  * Citations  — whether any of an entity's domains appears in the cited sources,
                 counting subdomains.
  * Sentiment  — optional, one cheap LLM call per mentioned entity. OFF for the
                 study: at 8 brands x 360 answers it is thousands of extra calls
                 answering a question the study never asks.

WHAT CHANGED (study extension):
  * Citation matching uses suffix logic, so work-study.flatironschool.com now
    counts toward flatironschool.com. Under exact matching those 71 citations
    scored as zero.
  * Entities can hold several domains.
  * Alias patterns are compiled once per entity instead of once per response.
    On a 1,022-row re-parse that is the difference between seconds and minutes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .config import Config, Entity
from .sources import domain_matches


@dataclass
class Mention:
    entity: str
    is_brand: bool
    count: int
    first_position: int | None  # char index of first hit; None if absent
    cited: bool
    sentiment: str | None = None


@lru_cache(maxsize=256)
def _compiled(alias_key: str) -> re.Pattern:
    aliases = alias_key.split("\x00")
    # Alphanumeric lookarounds give word-boundary behaviour that also works for
    # multi-word aliases and domain-like tokens ("Acme.com").
    parts = sorted((re.escape(a) for a in aliases), key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(parts) + r")(?![A-Za-z0-9])",
                      re.IGNORECASE)


def _alias_pattern(aliases: list[str]) -> re.Pattern:
    return _compiled("\x00".join(aliases))


def _count_mentions(text: str, entity: Entity) -> tuple[int, int | None]:
    matches = list(_alias_pattern(entity.aliases).finditer(text or ""))
    if not matches:
        return 0, None
    return len(matches), matches[0].start()


def entity_cited(entity: Entity, cited_domains: list[str]) -> bool:
    """True if any cited domain is, or is a subdomain of, one of the entity's."""
    for cited in cited_domains:
        for own in entity.domains:
            if domain_matches(cited, own):
                return True
    return False


def parse_response(text: str, cited_domains: list[str], cfg: Config) -> list[Mention]:
    mentions: list[Mention] = []
    brand_name = cfg.brand.name if cfg.brand else None
    for e in cfg.all_entities:
        count, pos = _count_mentions(text, e)
        mentions.append(Mention(
            entity=e.name,
            is_brand=(e.name == brand_name),
            count=count,
            first_position=pos,
            cited=entity_cited(e, cited_domains),
        ))
    if cfg.sentiment_enabled:
        _add_sentiment(text, mentions, cfg)
    return mentions


# --- optional sentiment -----------------------------------------------------

def _add_sentiment(text: str, mentions: list[Mention], cfg: Config) -> None:
    for m in mentions:
        if m.count > 0:
            m.sentiment = classify_sentiment(text, m.entity, cfg)


def classify_sentiment(text: str, entity: str, cfg: Config) -> str | None:
    """One cheap classification call. Returns positive/neutral/negative, or None."""
    prompt = (
        f"In the text below, how is '{entity}' portrayed? "
        f"Answer with exactly one word: positive, neutral, or negative.\n\n{text}"
    )
    try:
        if cfg.sentiment_provider == "openai":
            from openai import OpenAI
            out = OpenAI().responses.create(model=cfg.sentiment_model, input=prompt)
            word = (getattr(out, "output_text", "") or "").strip().lower()
        else:
            import anthropic
            msg = anthropic.Anthropic().messages.create(
                model=cfg.sentiment_model, max_tokens=5,
                messages=[{"role": "user", "content": prompt}],
            )
            word = "".join(b.text for b in msg.content
                           if getattr(b, "type", "") == "text").strip().lower()
        for label in ("positive", "negative", "neutral"):
            if label in word:
                return label
        return "neutral"
    except Exception:
        return None
