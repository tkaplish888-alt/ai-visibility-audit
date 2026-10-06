"""Study statistics. Everything the monitor's metrics.py cannot answer.

Jargon, defined once
--------------------
mention rate
    Share of answers in which a brand's name appears in the text.

citation rate
    Share of answers in which a brand's OWN domain appears as a source. A brand
    can be mentioned constantly and cited never, which means the model knows
    them from other people's content. That gap is the study's backbone.

Wilson score interval
    A rate measured from a sample has uncertainty around it. "22%" from 90
    answers really means "somewhere near 22%". The Wilson interval is the
    honest range. It is used instead of the textbook normal approximation
    because that one produces nonsense near 0% and 100%, such as intervals
    running below zero.

    The rule that matters: if two brands' intervals OVERLAP, you may not claim
    one beats the other. Say they are indistinguishable at this sample size.
    Getting this right is most of what makes a study look credible.

share of voice
    A brand's mentions as a fraction of all brand mentions. Borrowed from media
    measurement. Note this is a different definition from metrics.py, which
    normalizes rates instead of counting mentions. Both are defensible; the
    study reports this one and says so.

cross-model agreement
    For one prompt, do two models name the same brands? Measured as Jaccard
    overlap: the size of the shared brand set divided by the size of the
    combined set. 1.0 means identical, 0.0 means no overlap. Averaged across
    every model pair and prompt.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from itertools import combinations

try:
    from statsmodels.stats.proportion import proportion_confint
    _HAVE_SM = True
except ImportError:                                    # pragma: no cover
    _HAVE_SM = False


def wilson(hits: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """95% Wilson interval for a proportion. Falls back to a pure-Python
    implementation if statsmodels is not installed, so the module never breaks
    a run over a missing dependency."""
    if n == 0:
        return 0.0, 0.0
    if _HAVE_SM:
        low, high = proportion_confint(hits, n, alpha=alpha, method="wilson")
        return float(low), float(high)
    z = 1.959963984540054                              # alpha=0.05
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, centre - margin), min(1.0, centre + margin)


def intervals_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


@dataclass
class BrandStat:
    brand: str
    n: int
    mentions: int
    mention_rate: float
    mention_ci: tuple[float, float]
    own_citations: int
    citation_rate: float
    citation_ci: tuple[float, float]
    share_of_voice: float
    owned_ratio: float | None     # own-domain citations / mentions


def _connect(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _filters(since=None, until=None, engine=None, intent=None,
             exclude_truncated=False, exclude_engines=None):
    """Build the WHERE clause shared by every query.

    `exclude_engines` drops an engine from the figures while leaving its rows
    in the database. Used for Gemini, which answered 74% of questions without
    searching: its numbers are reported separately as an observation rather
    than deleted, because the observation is the point.
    """
    where, params = ["1=1"], []
    for e in (exclude_engines or []):
        where.append("r.engine != ?"); params.append(e)
    if since:
        where.append("r.run_ts >= ?"); params.append(since)
    if until:
        where.append("r.run_ts <= ?"); params.append(until)
    if engine:
        where.append("r.engine = ?"); params.append(engine)
    if intent:
        where.append("r.intent = ?"); params.append(intent)
    if exclude_truncated:
        where.append("COALESCE(r.truncated,0) = 0")
    return " AND ".join(where), params


def brand_stats(db: str, **kw) -> list[BrandStat]:
    """Mention and citation rates with Wilson intervals, per brand."""
    conn = _connect(db)
    clause, params = _filters(**kw)

    n = conn.execute(
        f"SELECT COUNT(*) FROM responses r WHERE {clause}", params).fetchone()[0]

    rows = conn.execute(f"""
        SELECT m.entity,
               SUM(CASE WHEN m.count > 0 THEN 1 ELSE 0 END) AS mentioned,
               SUM(CASE WHEN m.cited = 1 THEN 1 ELSE 0 END) AS cited,
               SUM(m.count)                                 AS raw_mentions
        FROM mentions m JOIN responses r ON r.id = m.response_id
        WHERE {clause}
        GROUP BY m.entity
    """, params).fetchall()
    conn.close()

    total_raw = sum(r["raw_mentions"] or 0 for r in rows) or 1
    out = []
    for r in rows:
        mentioned, cited = r["mentioned"] or 0, r["cited"] or 0
        out.append(BrandStat(
            brand=r["entity"],
            n=n,
            mentions=mentioned,
            mention_rate=mentioned / n if n else 0.0,
            mention_ci=wilson(mentioned, n),
            own_citations=cited,
            citation_rate=cited / n if n else 0.0,
            citation_ci=wilson(cited, n),
            share_of_voice=(r["raw_mentions"] or 0) / total_raw,
            owned_ratio=(cited / mentioned) if mentioned else None,
        ))
    out.sort(key=lambda b: b.mention_rate, reverse=True)
    return out


def source_mix(db: str, **kw) -> list[dict]:
    """Citation counts by source type, with share of all citations."""
    conn = _connect(db)
    clause, params = _filters(**kw)
    rows = conn.execute(f"""
        SELECT c.source_type, COUNT(*) AS n,
               COUNT(DISTINCT c.domain) AS domains
        FROM citations c JOIN responses r ON r.id = c.response_id
        WHERE {clause}
        GROUP BY c.source_type ORDER BY n DESC
    """, params).fetchall()
    conn.close()
    total = sum(r["n"] for r in rows) or 1
    return [{"source_type": r["source_type"] or "other", "citations": r["n"],
             "unique_domains": r["domains"], "share": r["n"] / total}
            for r in rows]


def top_domains(db: str, limit: int = 30, **kw) -> list[dict]:
    conn = _connect(db)
    clause, params = _filters(**kw)
    rows = conn.execute(f"""
        SELECT c.domain, c.source_type, c.owned_by, COUNT(*) AS n
        FROM citations c JOIN responses r ON r.id = c.response_id
        WHERE {clause}
        GROUP BY c.domain ORDER BY n DESC LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def by_intent(db: str, **kw) -> dict[str, list[BrandStat]]:
    conn = _connect(db)
    intents = [r[0] for r in conn.execute(
        "SELECT DISTINCT intent FROM responses WHERE intent IS NOT NULL")]
    conn.close()
    return {i: brand_stats(db, intent=i, **kw) for i in sorted(intents)}


def cross_model_agreement(db: str, **kw) -> dict:
    """Average Jaccard overlap of the brand sets each model returns per prompt.

    Needs two or more engines in the window. With one engine it returns None,
    which is the honest answer rather than a fabricated 1.0.
    """
    conn = _connect(db)
    clause, params = _filters(**kw)
    rows = conn.execute(f"""
        SELECT r.prompt_id, r.engine, m.entity
        FROM mentions m JOIN responses r ON r.id = m.response_id
        WHERE {clause} AND m.count > 0
    """, params).fetchall()
    conn.close()

    sets: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        sets.setdefault((r["prompt_id"], r["engine"]), set()).add(r["entity"])

    engines = sorted({e for _, e in sets})
    if len(engines) < 2:
        return {"engines": engines, "average_jaccard": None,
                "note": "needs 2+ engines in this window"}

    prompts = sorted({p for p, _ in sets})
    pairwise: dict[str, list[float]] = {}
    for a, b in combinations(engines, 2):
        scores = []
        for p in prompts:
            sa, sb = sets.get((p, a)), sets.get((p, b))
            if sa is None or sb is None:
                continue
            union = sa | sb
            scores.append(len(sa & sb) / len(union) if union else 1.0)
        if scores:
            pairwise[f"{a}|{b}"] = scores

    all_scores = [s for v in pairwise.values() for s in v]
    return {
        "engines": engines,
        "average_jaccard": sum(all_scores) / len(all_scores) if all_scores else None,
        "pairwise": {k: sum(v) / len(v) for k, v in pairwise.items()},
        "prompts_compared": len(prompts),
    }


def engine_summary(db: str, **kw) -> list[dict]:
    """Per-engine answer count, ungrounded answers (zero citations) and mean
    citations per answer. Pass `engine=` to restrict to one engine."""
    conn = _connect(db)
    clause, params = _filters(**kw)
    rows = conn.execute(f"""
        SELECT r.engine, COUNT(*) AS answers,
               SUM(CASE WHEN COALESCE(k.n, 0) = 0 THEN 1 ELSE 0 END) AS ungrounded,
               SUM(COALESCE(k.n, 0)) AS citations
        FROM responses r
        LEFT JOIN (SELECT response_id, COUNT(*) AS n
                   FROM citations GROUP BY response_id) k ON k.response_id = r.id
        WHERE {clause}
        GROUP BY r.engine ORDER BY r.engine
    """, params).fetchall()
    conn.close()
    return [{"engine": r["engine"], "answers": r["answers"],
             "ungrounded": r["ungrounded"], "citations": r["citations"],
             "avg_citations": r["citations"] / r["answers"] if r["answers"] else 0.0}
            for r in rows]


def significance_matrix(stats: list[BrandStat]) -> list[dict]:
    """For every brand pair, whether the mention-rate difference is claimable.

    This exists to stop you writing "A beats B" when the intervals overlap.
    """
    out = []
    for a, b in combinations(stats, 2):
        overlap = intervals_overlap(a.mention_ci, b.mention_ci)
        out.append({
            "a": a.brand, "b": b.brand,
            "a_rate": a.mention_rate, "b_rate": b.mention_rate,
            "distinguishable": not overlap,
            "verdict": ("indistinguishable at this sample size" if overlap
                        else f"{a.brand} > {b.brand}"),
        })
    return out


def as_dicts(stats: list[BrandStat]) -> list[dict]:
    return [asdict(s) for s in stats]
