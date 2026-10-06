"""One-time schema upgrade. Run once:

    python -m aeo.migrate                 # uses config.yaml's database
    python -m aeo.migrate --db aeo.db
    python -m aeo.migrate --no-backup     # skip the copy (not recommended)

It is additive and idempotent. Existing rows keep every value they had; new
columns start NULL and get filled by `python -m aeo.reparse`. Running it twice
does nothing the second time.

A timestamped backup of the database is written before anything is touched,
because six weeks of weekly runs is not something you want to re-collect.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .storage import migrate


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="aeo.migrate")
    p.add_argument("--db", default=None, help="path to the SQLite file")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--no-backup", action="store_true")
    args = p.parse_args(argv)

    db = args.db
    if db is None:
        from .config import load_config
        db = load_config(args.config).database

    path = Path(db)
    if not path.exists():
        print(f"No database at {path}. Nothing to migrate; it will be created "
              f"on the next run.")
        return

    if not args.no_backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_suffix(path.suffix + f".backup-{stamp}")
        shutil.copy2(path, backup)
        print(f"Backup written: {backup}")

    conn = sqlite3.connect(str(path))
    before = conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
    added = migrate(conn, verbose=True)
    after = conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
    conn.close()

    if added:
        print(f"Added {len(added)} column(s): {', '.join(added)}")
    else:
        print("Schema already current. Nothing to do.")
    print(f"Rows before: {before}   after: {after}   (must be equal)")
    print("\nNext: python -m aeo.reparse   (free, no API calls)")


if __name__ == "__main__":
    main()
