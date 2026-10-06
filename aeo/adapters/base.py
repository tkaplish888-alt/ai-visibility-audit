"""Stage 3 — Engine adapters (base).

Every adapter does one job: take a prompt, call a provider, return a normalized
EngineResult. That normalization is what keeps the rest of the system
engine-agnostic.

WHAT CHANGED (study extension):
  * Citations are now objects (url + title + domain), not bare domain strings.
    Bare domains threw away the specific page, which we need for source-type
    work and for anyone auditing the study.
  * `raw` carries the provider's own payload as plain JSON types. Storing it
    means every future parsing change is free instead of a paid re-run.
  * `model_version` records exactly which model answered, because "gpt-5"
    today and "gpt-5" in three months are not guaranteed to be the same thing.
  * `cited_domains` still exists as a derived property, so nothing downstream
    that already used it breaks.

Graceful failure: a missing key or provider error returns ok=False instead of
crashing the run. The runner now *records* those failures rather than dropping
them silently, so a gap in the data is visible instead of invisible.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class Citation:
    """One source the model linked to."""
    url: str
    title: str = ""
    domain: str = ""

    def __post_init__(self) -> None:
        if not self.domain:
            self.domain = to_domain(self.url)


@dataclass
class EngineResult:
    engine: str
    answer_text: str = ""
    citations: list[Citation] = field(default_factory=list)
    raw: dict | None = None
    model_version: str = ""
    ok: bool = True
    error: str | None = None

    @property
    def cited_domains(self) -> list[str]:
        """Back-compat: unique domains, order preserved."""
        out, seen = [], set()
        for c in self.citations:
            if c.domain and c.domain not in seen:
                seen.add(c.domain)
                out.append(c.domain)
        return out


def to_domain(url_or_host: str) -> str:
    """Normalize a URL or host to a bare domain (drop scheme/www/path/port)."""
    s = (url_or_host or "").strip()
    if not s:
        return ""
    if "://" not in s:
        s = "//" + s
    host = urlparse(s).netloc or urlparse(s).path
    host = host.split("/")[0].split("?")[0].lower()
    if ":" in host:
        host = host.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def build_citations(items: list) -> list[Citation]:
    """Accept URL strings or (url, title) pairs; dedupe by full URL.

    Deduping by URL rather than by domain is deliberate: two different Course
    Report pages are two citations, and collapsing them hid real volume.
    """
    out: list[Citation] = []
    seen: set[str] = set()
    for it in items:
        if isinstance(it, (tuple, list)):
            pair = list(it) + ["", ""]
            url, title = pair[0], pair[1]
        else:
            url, title = it, ""
        url = (url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        d = to_domain(url)
        if d:
            out.append(Citation(url=url, title=title or "", domain=d))
    return out


def dedupe_domains(urls: list[str]) -> list[str]:
    """Kept for any caller still expecting the old helper."""
    out, seen = [], set()
    for u in urls:
        d = to_domain(u)
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def jsonable(obj, _depth: int = 0):
    """Best-effort conversion of an SDK response object into plain JSON types.

    Provider SDKs return custom classes. Most expose model_dump() or to_dict();
    if not, we walk __dict__. Anything unconvertible becomes a string, so this
    never raises and never blocks a run.
    """
    if _depth > 12:
        return "<max-depth>"
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    for meth in ("model_dump", "to_dict", "dict"):
        fn = getattr(obj, meth, None)
        if callable(fn):
            try:
                return jsonable(fn(), _depth + 1)
            except Exception:
                pass
    if isinstance(obj, dict):
        return {str(k): jsonable(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v, _depth + 1) for v in obj]
    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict):
        return {str(k): jsonable(v, _depth + 1)
                for k, v in d.items() if not str(k).startswith("_")}
    return str(obj)


class Adapter:
    """Subclass and implement run(). Set `name` and optional `env_key`."""

    name = "base"
    env_key: str | None = None
    model: str = ""

    def available(self) -> bool:
        return self.env_key is None or bool(os.environ.get(self.env_key))

    def run(self, prompt: str) -> EngineResult:  # pragma: no cover - interface
        raise NotImplementedError


def get_adapter(name: str) -> Adapter:
    """Lazy registry — import provider SDKs only when the adapter is used."""
    name = name.lower()
    if name == "mock":
        from .mock_adapter import MockAdapter
        return MockAdapter()
    if name == "openai":
        from .openai_adapter import OpenAIAdapter
        return OpenAIAdapter()
    if name == "anthropic":
        from .anthropic_adapter import AnthropicAdapter
        return AnthropicAdapter()
    if name == "perplexity":
        from .perplexity_adapter import PerplexityAdapter
        return PerplexityAdapter()
    if name == "gemini":
        from .gemini_adapter import GeminiAdapter
        return GeminiAdapter()
    raise ValueError(f"unknown engine: {name}")
