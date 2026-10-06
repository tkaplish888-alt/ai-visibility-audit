"""Stage 1 — Config. `config.yaml` is the one file you edit; this module loads
it into the typed objects the rest of the pipeline relies on.

WHAT CHANGED (study extension):
  * Prompts are objects with an id and an intent tag, not bare strings. Without
    intent tags you cannot say "this brand owns comparison queries and vanishes
    from discovery queries", which is the most actionable cut in the data.
  * Entities carry a *list* of domains. General Assembly uses two; one string
    could never match both.
  * Both old and new YAML shapes load. Your existing config.yaml still works,
    so nothing breaks on the day you upgrade.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import yaml

# The five standard query intents. Anything outside this set is kept as-is but
# warned about, so a typo doesn't silently create a sixth category.
INTENTS = {
    "informational",      # "is a bootcamp worth it"
    "commercial",         # "best bootcamps for career changers"
    "comparative",        # "X vs Y"
    "navigational",       # "Flatiron tuition"
    "transactional",      # "how do I apply"
}


@dataclass
class Entity:
    name: str
    domains: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    @property
    def domain(self) -> str:
        """Back-compat for callers expecting a single domain."""
        return self.domains[0] if self.domains else ""


@dataclass
class Prompt:
    id: str
    text: str
    intent: str = "unclassified"


@dataclass
class Config:
    brand: Entity | None
    competitors: list[Entity]
    prompts: list[Prompt]
    runs_per_prompt: int
    engines: list[str]
    sentiment_enabled: bool
    sentiment_provider: str
    sentiment_model: str
    database: str
    study_mode: bool = False

    @property
    def all_entities(self) -> list[Entity]:
        if self.brand is None:
            return list(self.competitors)
        return [self.brand, *self.competitors]

    @property
    def owned_domains(self) -> dict[str, str]:
        """domain -> brand name, for every entity. Used by source typing."""
        out: dict[str, str] = {}
        for e in self.all_entities:
            for d in e.domains:
                if d:
                    out[d.lower()] = e.name
        return out

    @property
    def prompt_texts(self) -> list[str]:
        return [p.text for p in self.prompts]


def _entity(d: dict) -> Entity:
    name = d["name"]
    aliases = list(d.get("aliases") or [])
    if name not in aliases:
        aliases = [name, *aliases]
    # Accept `domains: [...]` (new) or `domain: "..."` (old).
    domains = list(d.get("domains") or [])
    if not domains and d.get("domain"):
        domains = [d["domain"]]
    return Entity(name=name, domains=[x.lower() for x in domains if x], aliases=aliases)


def _prompt_id(text: str) -> str:
    """Stable ID derived from the prompt text.

    Deriving rather than assigning means the IDs on your existing 1,022 rows
    are reproducible: re-parsing old data lands on the same IDs as a fresh run
    of the same prompt.
    """
    return "p" + hashlib.sha1(text.strip().lower().encode()).hexdigest()[:8]


def _prompt(item, index: int) -> Prompt:
    if isinstance(item, str):
        return Prompt(id=_prompt_id(item), text=item, intent="unclassified")
    text = item["text"] if "text" in item else item["prompt"]
    pid = str(item.get("id") or _prompt_id(text))
    intent = str(item.get("intent", "unclassified")).lower()
    if intent not in INTENTS and intent != "unclassified":
        print(f"  [warn] prompt {pid}: unrecognized intent '{intent}'")
    return Prompt(id=pid, text=text, intent=intent)


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    sentiment = raw.get("sentiment") or {}

    # Study mode: a flat `brands:` list with no privileged centre. Falls back
    # to the original brand/competitors split when `brands:` is absent.
    if raw.get("brands"):
        entities = [_entity(b) for b in raw["brands"]]
        brand, competitors, study_mode = None, entities, True
    else:
        brand = _entity(raw["brand"])
        competitors = [_entity(c) for c in raw.get("competitors", [])]
        study_mode = False

    prompts = [_prompt(p, i) for i, p in enumerate(raw["prompts"])]
    seen: set[str] = set()
    for p in prompts:
        if p.id in seen:
            raise ValueError(f"duplicate prompt id: {p.id} ({p.text[:50]})")
        seen.add(p.id)

    return Config(
        brand=brand,
        competitors=competitors,
        prompts=prompts,
        runs_per_prompt=int(raw.get("runs_per_prompt", 5)),
        engines=list(raw.get("engines", [])),
        sentiment_enabled=bool(sentiment.get("enabled", False)),
        sentiment_provider=sentiment.get("provider", "anthropic"),
        sentiment_model=sentiment.get("model", "claude-haiku-4-5"),
        database=raw.get("database", "aeo.db"),
        study_mode=study_mode,
    )
