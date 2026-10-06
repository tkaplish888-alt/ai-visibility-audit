"""Re-parse stored answers. No API calls, so this is free and repeatable.

    python -m aeo.reparse                # re-parse everything
    python -m aeo.reparse --dry-run      # report what would change
    python -m aeo.reparse --since 2026-08-01

This is the payoff for storing answer text. Every time the matcher improves,
the alias list grows, or a domain gets reclassified, you re-run this instead of
re-running the study.

What it rebuilds
----------------
  * mentions        — with the fixed suffix-based citation matching
  * citations       — one row per cited domain, with source type
  * prompt_id/intent — backfilled from config, so old rows join to new ones
  * truncated        — flags answers that stop mid-sentence

What it cannot recover
----------------------
Rows collected before the schema change have only bare domains, not full URLs.
So historic citations get domain-level source types and an empty URL. Runs
collected from here on carry the full URL. Say this in the study's limitations
section rather than pretending the two are identical.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3

from .config import Config, load_config
from .parsing import parse_response
from .sources import classify, owned_brand
from .storage import migrate

# An answer that stops on a lowercase letter, comma, or semicolon almost
# certainly hit the token ceiling rather than finishing its sentence.
_TRUNC_TAIL = re.compile(r"[a-z0-9,;:\-]$")


def looks_truncated(text: str) -> bool:
    t = (text or "").rstrip()
    if not t:
        return False
    return bool(_TRUNC_TAIL.search(t))


def _prompt_lookup(cfg: Config) -> dict[str, tuple[str, str]]:
    """prompt text (normalized) -> (prompt_id, intent)."""
    return {p.text.strip().lower(): (p.id, p.intent) for p in cfg.prompts}


def reparse(db_path: str, cfg: Config, since: str | None = None,
            dry_run: bool = False, verbose: bool = True) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrate(conn)

    where, params = ["1=1"], []
    if since:
        where.append("run_ts >= ?")
        params.append(since)
    clause = " AND ".join(where)

    rows = conn.execute(
        f"SELECT id, prompt, answer_text, cited_domains FROM responses "
        f"WHERE {clause}", params).fetchall()

    lookup = _prompt_lookup(cfg)
    owned = cfg.owned_domains

    stats = {
        "responses": len(rows),
        "mentions_written": 0,
        "citations_written": 0,
        "truncated": 0,
        "prompt_ids_filled": 0,
        "unmatched_prompts": set(),
        "citation_delta": 0,   # citations gained by the suffix-match fix
    }

    for r in rows:
        text = r["answer_text"] or ""
        domains = json.loads(r["cited_domains"] or "[]")

        before = conn.execute(
            "SELECT COUNT(*) FROM mentions WHERE response_id=? AND cited=1",
            (r["id"],)).fetchone()[0]

        mentions = parse_response(text, domains, cfg)
        after = sum(1 for m in mentions if m.cited)
        stats["citation_delta"] += after - before

        pid, intent = lookup.get((r["prompt"] or "").strip().lower(), (None, None))
        if pid is None:
            stats["unmatched_prompts"].add((r["prompt"] or "")[:70])
        else:
            stats["prompt_ids_filled"] += 1

        trunc = looks_truncated(text)
        if trunc:
            stats["truncated"] += 1

        if dry_run:
            stats["mentions_written"] += len(mentions)
            stats["citations_written"] += len(domains)
            continue

        conn.execute("DELETE FROM mentions WHERE response_id = ?", (r["id"],))
        conn.executemany(
            "INSERT INTO mentions(response_id,entity,is_brand,count,"
            "first_position,cited,sentiment) VALUES (?,?,?,?,?,?,?)",
            [(r["id"], m.entity, int(m.is_brand), m.count, m.first_position,
              int(m.cited), m.sentiment) for m in mentions])
        stats["mentions_written"] += len(mentions)

        conn.execute("DELETE FROM citations WHERE response_id = ?", (r["id"],))
        cite_rows = [
            (r["id"], "", "", d, classify(d, owned), owned_brand(d, owned), i)
            for i, d in enumerate(domains) if d
        ]
        conn.executemany(
            "INSERT INTO citations(response_id,url,title,domain,source_type,"
            "owned_by,position) VALUES (?,?,?,?,?,?,?)", cite_rows)
        stats["citations_written"] += len(cite_rows)

        conn.execute(
            "UPDATE responses SET prompt_id=COALESCE(?,prompt_id), "
            "intent=COALESCE(?,intent), truncated=?, n_citations=? WHERE id=?",
            (pid, intent, int(trunc), len(cite_rows), r["id"]))

    if not dry_run:
        conn.commit()
    conn.close()

    if verbose:
        mode = "DRY RUN — nothing written" if dry_run else "written"
        print(f"\nRe-parse ({mode})")
        print(f"  responses processed : {stats['responses']}")
        print(f"  mentions            : {stats['mentions_written']}")
        print(f"  citation rows       : {stats['citations_written']}")
        print(f"  truncated answers   : {stats['truncated']} "
              f"({stats['truncated'] / max(stats['responses'], 1):.0%})")
        print(f"  prompt ids filled   : {stats['prompt_ids_filled']}")
        delta = stats["citation_delta"]
        if delta:
            print(f"  citations recovered by suffix matching: {delta:+d}")
        if stats["unmatched_prompts"]:
            print(f"  [warn] {len(stats['unmatched_prompts'])} prompt(s) in the "
                  f"database are not in config.yaml:")
            for p in sorted(stats["unmatched_prompts"])[:8]:
                print(f"         - {p}")
    return stats


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="aeo.reparse")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--db", default=None)
    p.add_argument("--since", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    reparse(args.db or cfg.database, cfg, since=args.since, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
