"""Stage 5 — Metrics. Queries over the time series. Any view ("share of voice
last quarter") is just a filtered aggregate here.

Definitions (all per selected window/engine):
  mention_rate   = responses where entity mentioned / total responses
  citation_rate  = responses where entity's domain cited / total responses
  share_of_voice = entity mention_rate / sum of all entities' mention_rate
  avg_position   = mean first_position when mentioned (lower = more prominent)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict


@dataclass
class EntityMetrics:
    entity: str
    is_brand: bool
    responses: int
    mentions: int
    mention_rate: float
    citation_rate: float
    share_of_voice: float
    avg_position: float | None
    sentiment_breakdown: dict


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def compute(db_path: str, since: str | None = None, until: str | None = None,
            engine: str | None = None) -> list[EntityMetrics]:
    conn = _connect(db_path)
    where, params = ["1=1"], []
    if since:
        where.append("r.run_ts >= ?"); params.append(since)
    if until:
        where.append("r.run_ts <= ?"); params.append(until)
    if engine:
        where.append("r.engine = ?"); params.append(engine)
    clause = " AND ".join(where)

    total = conn.execute(
        f"SELECT COUNT(*) FROM responses r WHERE {clause}", params
    ).fetchone()[0]

    rows = conn.execute(f"""
        SELECT m.entity,
               MAX(m.is_brand)                              AS is_brand,
               SUM(CASE WHEN m.count > 0 THEN 1 ELSE 0 END) AS mentioned,
               SUM(CASE WHEN m.cited = 1 THEN 1 ELSE 0 END) AS cited,
               AVG(CASE WHEN m.count > 0 THEN m.first_position END) AS avg_pos
        FROM mentions m JOIN responses r ON r.id = m.response_id
        WHERE {clause}
        GROUP BY m.entity
    """, params).fetchall()

    # Sentiment tallies per entity.
    sent_rows = conn.execute(f"""
        SELECT m.entity, m.sentiment, COUNT(*) AS n
        FROM mentions m JOIN responses r ON r.id = m.response_id
        WHERE {clause} AND m.sentiment IS NOT NULL
        GROUP BY m.entity, m.sentiment
    """, params).fetchall()
    conn.close()

    sentiment: dict[str, dict] = {}
    for sr in sent_rows:
        sentiment.setdefault(sr["entity"], {})[sr["sentiment"]] = sr["n"]

    mention_rates = {r["entity"]: (r["mentioned"] / total if total else 0.0) for r in rows}
    sov_denom = sum(mention_rates.values()) or 1.0

    out = []
    for r in rows:
        mr = mention_rates[r["entity"]]
        out.append(EntityMetrics(
            entity=r["entity"],
            is_brand=bool(r["is_brand"]),
            responses=total,
            mentions=r["mentioned"],
            mention_rate=round(mr, 4),
            citation_rate=round(r["cited"] / total if total else 0.0, 4),
            share_of_voice=round(mr / sov_denom, 4),
            avg_position=round(r["avg_pos"], 1) if r["avg_pos"] is not None else None,
            sentiment_breakdown=sentiment.get(r["entity"], {}),
        ))
    out.sort(key=lambda e: e.mention_rate, reverse=True)
    return out


def as_dicts(metrics: list[EntityMetrics]) -> list[dict]:
    return [asdict(m) for m in metrics]
