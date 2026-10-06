"""Mock engine — run the whole pipeline end to end with no API keys.

Returns varied, non-deterministic answers so you can see rates ("3 of 5") and
every metric working before wiring real providers, and before spending money.
"""
from __future__ import annotations

import random

from .base import Adapter, EngineResult, build_citations

_POOL = [
    ("For small teams, {a} is a strong choice, though {b} is also popular.",
     [("https://flatironschool.com/programs", "Programs"),
      ("https://coursereport.com/schools/general-assembly", "GA reviews")]),
    ("Most people recommend {b} or {c}. {a} is worth a look too.",
     [("https://generalassemb.ly/students", "Students"),
      ("https://reddit.com/r/codingbootcamp/x", "Thread")]),
    ("{c} leads this category. {a} and {b} are solid alternatives.",
     [("https://hackreactor.com/", "Hack Reactor"),
      ("https://work-study.flatironschool.com/", "Work study")]),
    ("Top picks include {a}, {b}, and {c}, each with tradeoffs.",
     [("https://careerkarma.com/rankings", "Rankings"),
      ("https://switchup.org/bootcamps", "Bootcamps"),
      ("https://youtube.com/watch?v=x", "Review")]),
    ("{b} is the market leader; {c} is cheaper.",
     [("https://forbes.com/advisor/bootcamps", "Advisor")]),
]


class MockAdapter(Adapter):
    name = "mock"
    env_key = None          # always available
    model = "mock-1"

    def run(self, prompt: str) -> EngineResult:
        template, sources = random.choice(_POOL)
        text = template.format(a="Flatiron School", b="General Assembly",
                               c="Hack Reactor")
        # Occasionally drop a citation to make citation rate interesting.
        kept = [s for s in sources if random.random() > 0.25]
        return EngineResult(
            engine=self.name,
            answer_text=text,
            citations=build_citations(kept),
            raw={"mock": True, "prompt": prompt},
            model_version=self.model,
        )
