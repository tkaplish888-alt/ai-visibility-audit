"""Stage 5 — Storage. Append-only, timestamped SQLite so we build a time series,
never a snapshot. Every run appends rows; nothing is overwritten.

WHAT CHANGED (study extension):
  * `responses.raw` stores the provider's own payload. This is the single most
    important change in the whole extension: without it, any change to the
    parsing logic means re-running the study and paying for it again. With it,
    re-parsing is free and unlimited.
  * `responses.prompt_id` / `intent` / `model_version` let you slice by query
    type and pin which model actually answered.
  * New `citations` table: one row per cited URL, with the full URL, the page
    title, the domain, and the source type. The old design kept only a JSON
    array of bare domains, which threw away the page and made source-type
    analysis impossible.
  * New `failures` table: a call that errors is now recorded instead of
    vanishing. Silent gaps in a dataset are how studies end up wrong.
  * `truncated` flags answers that hit the token ceiling, so a known-bad row
    can be excluded from position metrics rather than quietly skewing them.

Migration is additive and safe. Existing rows keep every value they had; new
columns start NULL. Run `python -m aeo.migrate` once.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .parsing import Mention

SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ts        TEXT NOT NULL,      -- ISO8601 UTC, one value per pipeline run
    prompt        TEXT NOT NULL,
    engine        TEXT NOT NULL,
    run_index     INTEGER NOT NULL,   -- 0..N-1 within this run/prompt/engine
    answer_text   TEXT,
    cited_domains TEXT                 -- JSON array (kept for back-compat)
);
CREATE TABLE IF NOT EXISTS mentions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id    INTEGER NOT NULL REFERENCES responses(id),
    entity         TEXT NOT NULL,
    is_brand       INTEGER NOT NULL,
    count          INTEGER NOT NULL,
    first_position INTEGER,
    cited          INTEGER NOT NULL,
    sentiment      TEXT
);
CREATE TABLE IF NOT EXISTS citations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id  INTEGER NOT NULL REFERENCES responses(id),
    url          TEXT,
    title        TEXT,
    domain       TEXT NOT NULL,
    source_type  TEXT,
    owned_by     TEXT,                -- brand name if this is a brand's own site
    position     INTEGER              -- order the provider returned it in
);
CREATE TABLE IF NOT EXISTS failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ts      TEXT NOT NULL,
    prompt_id   TEXT,
    prompt      TEXT,
    engine      TEXT NOT NULL,
    run_index   INTEGER,
    error       TEXT,
    occurred_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_resp_ts    ON responses(run_ts);
CREATE INDEX IF NOT EXISTS idx_ment_resp  ON mentions(response_id);
CREATE INDEX IF NOT EXISTS idx_cite_resp  ON citations(response_id);
CREATE INDEX IF NOT EXISTS idx_cite_dom   ON citations(domain);
CREATE INDEX IF NOT EXISTS idx_cite_type  ON citations(source_type);
"""

# Columns added to `responses` after the original build. Additive only.
NEW_RESPONSE_COLUMNS = [
    ("prompt_id",     "TEXT"),
    ("intent",        "TEXT"),
    ("model_version", "TEXT"),
    ("raw",           "TEXT"),   # provider payload, JSON
    ("truncated",     "INTEGER"),
    ("n_citations",   "INTEGER"),
    ("parsed_at",     "TEXT"),
]


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection, verbose: bool = False) -> list[str]:
    """Add any missing columns and tables. Safe to run repeatedly."""
    conn.executescript(SCHEMA)
    added = []
    have = _existing_columns(conn, "responses")
    for col, coltype in NEW_RESPONSE_COLUMNS:
        if col not in have:
            conn.execute(f"ALTER TABLE responses ADD COLUMN {col} {coltype}")
            added.append(col)
            if verbose:
                print(f"  + responses.{col}")
    conn.commit()
    return added


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        migrate(self.conn)

    @staticmethod
    def now_ts() -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- writes ------------------------------------------------------------

    def add_response(self, run_ts: str, prompt: str, engine: str, run_index: int,
                     answer_text: str, cited_domains: list[str],
                     mentions: list[Mention],
                     prompt_id: str | None = None, intent: str | None = None,
                     model_version: str | None = None, raw: dict | None = None,
                     citations: list | None = None,
                     truncated: bool | None = None,
                     owned_domains: dict[str, str] | None = None) -> int:
        """Store one answer plus its parsed mentions and citations.

        `citations` takes Citation objects from the adapter. `cited_domains`
        is still written so older queries and the existing dashboard keep
        working unchanged.
        """
        from .sources import classify, owned_brand

        cur = self.conn.execute(
            "INSERT INTO responses(run_ts,prompt,engine,run_index,answer_text,"
            "cited_domains,prompt_id,intent,model_version,raw,truncated,"
            "n_citations,parsed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_ts, prompt, engine, run_index, answer_text,
             json.dumps(cited_domains), prompt_id, intent, model_version,
             json.dumps(raw) if raw is not None else None,
             int(bool(truncated)) if truncated is not None else None,
             len(citations or []), self.now_ts()),
        )
        rid = cur.lastrowid

        self.conn.executemany(
            "INSERT INTO mentions(response_id,entity,is_brand,count,first_position,"
            "cited,sentiment) VALUES (?,?,?,?,?,?,?)",
            [(rid, m.entity, int(m.is_brand), m.count, m.first_position,
              int(m.cited), m.sentiment) for m in mentions],
        )

        if citations:
            rows = []
            for i, c in enumerate(citations):
                domain = getattr(c, "domain", "") or ""
                rows.append((rid, getattr(c, "url", ""), getattr(c, "title", ""),
                             domain, classify(domain, owned_domains),
                             owned_brand(domain, owned_domains or {}), i))
            self.conn.executemany(
                "INSERT INTO citations(response_id,url,title,domain,source_type,"
                "owned_by,position) VALUES (?,?,?,?,?,?,?)", rows)

        self.conn.commit()
        return rid

    def add_failure(self, run_ts: str, engine: str, error: str,
                    prompt: str = "", prompt_id: str | None = None,
                    run_index: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO failures(run_ts,prompt_id,prompt,engine,run_index,error,"
            "occurred_at) VALUES (?,?,?,?,?,?,?)",
            (run_ts, prompt_id, prompt, engine, run_index, str(error)[:2000],
             self.now_ts()),
        )
        self.conn.commit()

    # --- reads -------------------------------------------------------------

    def completed_keys(self, run_ts: str) -> set[tuple[str, str, int]]:
        """(prompt_id, engine, run_index) already stored for this run.

        Resumability: if a run dies at call 200 of 360, restarting it skips
        everything already paid for instead of starting over.
        """
        rows = self.conn.execute(
            "SELECT prompt_id, engine, run_index FROM responses WHERE run_ts = ?",
            (run_ts,)).fetchall()
        return {(r["prompt_id"], r["engine"], r["run_index"]) for r in rows}

    def close(self) -> None:
        self.conn.close()
