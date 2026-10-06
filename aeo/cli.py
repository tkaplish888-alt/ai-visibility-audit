"""Command-line entry point.

  python -m aeo.cli run                 # hit the configured engines
  python -m aeo.cli run --dry-run       # mock engine, no API keys, no cost
  python -m aeo.cli run --resume TS     # continue a run that died partway
  python -m aeo.cli smoke               # check every engine is grounded
  python -m aeo.cli report              # monitor metrics (as before)
  python -m aeo.cli study               # study stats with confidence intervals
  python -m aeo.cli study --intent comparative
  python -m aeo.cli sources             # citation source mix + top domains
"""
from __future__ import annotations

import argparse
import dataclasses

from . import stats as study_stats
from .config import load_config
from .metrics import compute
from .runner import list_models, run_pipeline, smoke_test


def _print_report(metrics) -> None:
    if not metrics:
        print("No data yet. Run the pipeline first.")
        return
    print(f"\nResponses in window: {metrics[0].responses}\n")
    hdr = f"{'entity':<12}{'mention':>9}{'citation':>10}{'SoV':>8}{'avg_pos':>9}  sentiment"
    print(hdr)
    print("-" * len(hdr))
    for m in metrics:
        star = "*" if m.is_brand else " "
        pos = f"{m.avg_position}" if m.avg_position is not None else "-"
        sent = ", ".join(f"{k}:{v}" for k, v in m.sentiment_breakdown.items()) or "-"
        print(f"{star}{m.entity:<11}{m.mention_rate:>8.0%}{m.citation_rate:>10.0%}"
              f"{m.share_of_voice:>8.0%}{pos:>9}  {sent}")
    print("\n* = your brand")


def _print_study(rows) -> None:
    if not rows:
        print("No data in this window.")
        return
    print(f"\nn = {rows[0].n} answers\n")
    hdr = (f"{'brand':<20}{'mention':>9}{'95% CI':>18}{'own cites':>11}"
           f"{'cite rate':>11}{'SoV':>8}")
    print(hdr)
    print("-" * len(hdr))
    for b in rows:
        ci = f"[{b.mention_ci[0]:.1%}, {b.mention_ci[1]:.1%}]"
        print(f"{b.brand:<20}{b.mention_rate:>8.1%}{ci:>18}{b.own_citations:>11}"
              f"{b.citation_rate:>10.1%}{b.share_of_voice:>8.1%}")
    print("\nOverlapping intervals mean the difference is not claimable at this "
          "sample size.")


def _print_sources(db, **kw) -> None:
    print("\nCitation source mix\n")
    for s in study_stats.source_mix(db, **kw):
        print(f"  {s['source_type']:<20}{s['citations']:>7}{s['share']:>8.1%}"
              f"   ({s['unique_domains']} domains)")
    print("\nTop cited domains\n")
    for d in study_stats.top_domains(db, limit=25, **kw):
        own = f"  <- {d['owned_by']}" if d["owned_by"] else ""
        print(f"  {d['n']:>5}  {d['domain']:<38}{d['source_type'] or 'other'}{own}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="aeo")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run the measurement pipeline")
    pr.add_argument("--dry-run", action="store_true",
                    help="use the mock engine instead of real APIs")
    pr.add_argument("--resume", default=None, metavar="RUN_TS",
                    help="continue an interrupted run, skipping stored calls")
    pr.add_argument("--sleep", type=float, default=1.0,
                    help="seconds between calls (rate limiting)")

    sub.add_parser("smoke", help="one call per engine; verifies grounding is on")
    sub.add_parser("models", help="list model IDs each provider will accept")

    rp = sub.add_parser("report", help="monitor metrics from stored data")
    rp.add_argument("--since"); rp.add_argument("--until"); rp.add_argument("--engine")

    st = sub.add_parser("study", help="study stats with confidence intervals")
    st.add_argument("--since"); st.add_argument("--until"); st.add_argument("--engine")
    st.add_argument("--intent")
    st.add_argument("--exclude-truncated", action="store_true")
    st.add_argument("--exclude-engine", action="append", default=[])

    sc = sub.add_parser("sources", help="citation source mix and top domains")
    sc.add_argument("--since"); sc.add_argument("--until"); sc.add_argument("--engine")
    sc.add_argument("--exclude-engine", action="append", default=[])

    args = p.parse_args(argv)
    cfg = load_config(args.config)

    if args.cmd == "run":
        if args.dry_run:
            cfg = dataclasses.replace(cfg, engines=["mock"])
        run_pipeline(cfg, resume_ts=args.resume, sleep_seconds=args.sleep)
    elif args.cmd == "smoke":
        smoke_test(cfg)
    elif args.cmd == "models":
        list_models()
    elif args.cmd == "report":
        _print_report(compute(cfg.database, args.since, args.until, args.engine))
    elif args.cmd == "study":
        _print_study(study_stats.brand_stats(
            cfg.database, since=args.since, until=args.until,
            engine=args.engine, intent=args.intent,
            exclude_truncated=args.exclude_truncated,
            exclude_engines=args.exclude_engine))
    elif args.cmd == "sources":
        _print_sources(cfg.database, since=args.since, until=args.until,
                       engine=args.engine, exclude_engines=args.exclude_engine)


if __name__ == "__main__":
    main()
