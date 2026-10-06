"""The doorbell.

A weekly cron that appends rows to a database and waits for you to go look at
a dashboard is not a monitor. It is a chore with extra steps. This module
compares the newest run to the one before it and emails you only when
something crosses a line you defined. The rest of the time it stays quiet.

    python -m aeo.alerts --check          # print what would be sent
    python -m aeo.alerts --send           # actually email
    python -m aeo.alerts --send --always  # email even with nothing to report

Email is plain SMTP, so it works with Gmail app passwords, Fastmail, or any
provider. Set these in the environment (and as GitHub Actions secrets):

    ALERT_SMTP_HOST      smtp.gmail.com
    ALERT_SMTP_PORT      587
    ALERT_SMTP_USER      you@example.com
    ALERT_SMTP_PASSWORD  an app password, never your account password
    ALERT_TO             where to send it

If SMTP is not configured, --send prints the message instead of failing, so a
missing secret never breaks the cron.
"""
from __future__ import annotations

import argparse
import os
import smtplib
import sqlite3
from dataclasses import dataclass
from email.message import EmailMessage

from .config import load_config
from .stats import brand_stats, wilson

# --- what counts as "wrong". Edit these; they are the whole point. ---------
THRESHOLDS = {
    # Absolute percentage-point change in a brand's mention rate week to week.
    # 8 points is roughly the noise floor at n=175, so smaller moves are noise.
    "mention_rate_delta": 0.08,
    # Same for citation rate.
    "citation_rate_delta": 0.08,
    # A domain entering or leaving the top N cited sources.
    "top_domains_watched": 10,
    # Share of answers with zero citations. Above this, grounding is failing
    # and the run's data is suspect.
    "ungrounded_share": 0.20,
    # Any failed API calls at all in the latest run.
    "alert_on_failures": True,
}


@dataclass
class Alert:
    severity: str        # "high" | "medium" | "info"
    headline: str
    detail: str


def _runs(conn: sqlite3.Connection, limit: int = 2) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT run_ts FROM responses ORDER BY run_ts DESC LIMIT ?",
        (limit,))]


def _top_domains(conn, run_ts, n) -> list[str]:
    return [r[0] for r in conn.execute("""
        SELECT c.domain FROM citations c JOIN responses r ON r.id = c.response_id
        WHERE r.run_ts = ? GROUP BY c.domain ORDER BY COUNT(*) DESC LIMIT ?
    """, (run_ts, n))]


def _ungrounded_share(conn, run_ts) -> float:
    total = conn.execute("SELECT COUNT(*) FROM responses WHERE run_ts=?",
                         (run_ts,)).fetchone()[0]
    if not total:
        return 0.0
    zero = conn.execute(
        "SELECT COUNT(*) FROM responses WHERE run_ts=? AND COALESCE(n_citations,0)=0",
        (run_ts,)).fetchone()[0]
    return zero / total


def check(db_path: str) -> tuple[list[Alert], str, str | None]:
    """Compare the two most recent runs. Returns (alerts, latest, previous)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    runs = _runs(conn)
    if not runs:
        conn.close()
        return [], "", None
    latest = runs[0]
    previous = runs[1] if len(runs) > 1 else None

    alerts: list[Alert] = []

    # --- data quality first. A quality problem invalidates everything else. --
    ug = _ungrounded_share(conn, latest)
    if ug > THRESHOLDS["ungrounded_share"]:
        alerts.append(Alert(
            "high", f"{ug:.0%} of answers cited nothing",
            "Grounding may have failed. Check whether web search is enabled "
            "for each engine before trusting this run."))

    if THRESHOLDS["alert_on_failures"]:
        fails = conn.execute(
            "SELECT engine, COUNT(*) n FROM failures WHERE run_ts=? GROUP BY engine",
            (latest,)).fetchall()
        if fails:
            detail = ", ".join(f"{r['engine']}: {r['n']}" for r in fails)
            alerts.append(Alert("high", "API calls failed in this run", detail))

    if previous is None:
        conn.close()
        alerts.append(Alert("info", "First run stored",
                            "No previous run to compare against yet."))
        return alerts, latest, None

    # --- brand movement ----------------------------------------------------
    now = {b.brand: b for b in brand_stats(db_path, since=latest, until=latest)}
    then = {b.brand: b for b in brand_stats(db_path, since=previous, until=previous)}

    for name, cur in now.items():
        old = then.get(name)
        if old is None:
            alerts.append(Alert("medium", f"{name} appeared for the first time",
                                f"Mention rate {cur.mention_rate:.0%}."))
            continue

        d = cur.mention_rate - old.mention_rate
        if abs(d) >= THRESHOLDS["mention_rate_delta"]:
            # Only flag it if the intervals do not overlap. Otherwise the move
            # is inside the noise and flagging it trains you to ignore alerts.
            if not (cur.mention_ci[0] <= old.mention_ci[1]
                    and old.mention_ci[0] <= cur.mention_ci[1]):
                alerts.append(Alert(
                    "high" if abs(d) >= 0.15 else "medium",
                    f"{name} mention rate {'up' if d > 0 else 'down'} "
                    f"{abs(d):.0%}",
                    f"{old.mention_rate:.0%} to {cur.mention_rate:.0%}. "
                    f"Intervals do not overlap, so this is a real move."))

        dc = cur.citation_rate - old.citation_rate
        if abs(dc) >= THRESHOLDS["citation_rate_delta"]:
            alerts.append(Alert(
                "medium",
                f"{name} own-site citation rate "
                f"{'up' if dc > 0 else 'down'} {abs(dc):.0%}",
                f"{old.citation_rate:.0%} to {cur.citation_rate:.0%}."))

    # --- source movement ---------------------------------------------------
    n = THRESHOLDS["top_domains_watched"]
    new_top, old_top = _top_domains(conn, latest, n), _top_domains(conn, previous, n)
    entered, left = set(new_top) - set(old_top), set(old_top) - set(new_top)
    if entered:
        alerts.append(Alert("medium", f"New domain(s) in the top {n} sources",
                            ", ".join(sorted(entered))))
    if left:
        alerts.append(Alert("info", f"Dropped out of the top {n} sources",
                            ", ".join(sorted(left))))

    conn.close()
    return alerts, latest, previous


def render(alerts: list[Alert], latest: str, previous: str | None) -> tuple[str, str]:
    high = sum(1 for a in alerts if a.severity == "high")
    if not alerts:
        subject = "AEO: nothing to report"
    elif high:
        subject = f"AEO: {high} thing(s) need attention"
    else:
        subject = f"AEO: {len(alerts)} change(s) this week"

    lines = [f"Run: {latest}"]
    if previous:
        lines.append(f"Compared against: {previous}")
    lines.append("")
    if not alerts:
        lines.append("No thresholds crossed. Nothing needs you.")
    else:
        for sev in ("high", "medium", "info"):
            group = [a for a in alerts if a.severity == sev]
            if not group:
                continue
            lines.append(sev.upper())
            for a in group:
                lines.append(f"  - {a.headline}")
                lines.append(f"    {a.detail}")
            lines.append("")
    lines.append("Thresholds live in aeo/alerts.py. If this email was noise, "
                 "raise them.")
    return subject, "\n".join(lines)


def send(subject: str, body: str) -> bool:
    host = os.environ.get("ALERT_SMTP_HOST")
    user = os.environ.get("ALERT_SMTP_USER")
    pw = os.environ.get("ALERT_SMTP_PASSWORD")
    to = os.environ.get("ALERT_TO") or user
    if not (host and user and pw and to):
        print("[alerts] SMTP not configured; printing instead.\n")
        print(f"Subject: {subject}\n\n{body}")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    port = int(os.environ.get("ALERT_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)
    print(f"[alerts] sent to {to}")
    return True


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="aeo.alerts")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--db", default=None)
    p.add_argument("--send", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--always", action="store_true",
                   help="send even when nothing crossed a threshold")
    args = p.parse_args(argv)

    db = args.db or load_config(args.config).database
    alerts, latest, previous = check(db)
    subject, body = render(alerts, latest, previous)

    if args.send and (alerts or args.always):
        send(subject, body)
    else:
        print(f"Subject: {subject}\n\n{body}")
        if args.send:
            print("\n(Nothing crossed a threshold, so nothing was sent. "
                  "Use --always to send anyway.)")


if __name__ == "__main__":
    main()
