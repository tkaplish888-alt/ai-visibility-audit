"""Stage 6 — Static HTML report.

Reads the append-only SQLite time series and renders a single self-contained
`public/index.html`: a current snapshot from the most recent run plus a real
trend chart (inline SVG) across every run stored so far.

    python3 -m aeo.report_html                 # aeo.db -> public/index.html
    python3 -m aeo.report_html --db aeo.db --out public/index.html

Everything on the page is derived from the database — there are no hardcoded
numbers. With a single run the trend degrades honestly to one plotted point
with a "week 1 of an accumulating series" note; it never renders empty.
"""
from __future__ import annotations

import argparse
import html
import math
import os
import sqlite3
from datetime import datetime

import yaml

from .metrics import EntityMetrics, compute

# How raw engine ids present to a human-facing page.
ENGINE_DISPLAY = {
    "anthropic": "Claude",
    "openai": "ChatGPT",
    "perplexity": "Perplexity",
    "gemini": "Gemini",
    "mock": "the mock engine",
}

# Competitor line colors for the trend chart — quiet slate tones so the two
# brand lines (indigo) stay dominant.
COMPETITOR_COLORS = ["#94A3B8", "#B4BECD", "#CBD5E1"]

BRAND = "#4F46E5"
SLATE = "#B4BECD"


# --------------------------------------------------------------------------- #
# Data gathering
# --------------------------------------------------------------------------- #

def _run_timestamps(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT run_ts FROM responses ORDER BY run_ts")]


def _run_info(conn: sqlite3.Connection, ts: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT prompt) AS q, "
        "COUNT(DISTINCT engine) AS e FROM responses WHERE run_ts = ?",
        (ts,)).fetchone()
    engines = [r[0] for r in conn.execute(
        "SELECT DISTINCT engine FROM responses WHERE run_ts = ? ORDER BY engine",
        (ts,))]
    n, q, e = row["n"], row["q"], row["e"]
    return {
        "ts": ts,
        "responses": n,
        "prompts": q,
        "engines": engines,
        "runs_per_prompt": round(n / (q * e)) if q and e else 0,
    }


def _engine_label(engines: list[str]) -> str:
    names = [ENGINE_DISPLAY.get(e, e.title()) for e in engines]
    if not names:
        return "AI engines"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " & " + names[-1]


def _brand_metrics(metrics: list[EntityMetrics]) -> EntityMetrics | None:
    for m in metrics:
        if m.is_brand:
            return m
    return None


def _fmt_date(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%b %-d, %Y")
    except ValueError:
        return ts[:10]


def _load_meta(config_path: str) -> dict:
    """Brand display info and optional category label from config.yaml.

    Only qualitative labels come from here — never metrics.
    """
    meta = {"brand_name": "our brand", "brand_short": "our brand",
            "domain": "", "category": "our category"}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return meta
    brand = raw.get("brand") or {}
    name = brand.get("name") or meta["brand_name"]
    meta["brand_name"] = name
    meta["brand_short"] = name.split()[0] if name else name
    meta["domain"] = brand.get("domain", "")
    if raw.get("category"):
        meta["category"] = str(raw["category"])
    return meta


# --------------------------------------------------------------------------- #
# SVG trend chart
# --------------------------------------------------------------------------- #

def _nice_ceiling(v: float) -> float:
    """Round a max value up to a clean 10%% grid ceiling (min 10%)."""
    if v <= 0:
        return 0.1
    return max(0.1, math.ceil(v * 10) / 10)


def _svg_trend(series: list[dict], labels: list[str]) -> str:
    """Inline SVG line chart. `series` items: {name,color,dash,width,vals}.

    Degrades to plotted dots when there is a single run — never empty.
    """
    W, H = 720, 300
    L, R, T, B = 46, 20, 18, 42
    pw, ph = W - L - R, H - T - B
    n = len(labels)

    all_vals = [v for s in series for v in s["vals"]]
    ymax = _nice_ceiling(max(all_vals) if all_vals else 0.1)

    def X(i: int) -> float:
        if n <= 1:
            return L + pw * 0.10
        return L + pw * i / (n - 1)

    def Y(v: float) -> float:
        return T + ph * (1 - (v / ymax if ymax else 0))

    p: list[str] = []
    # Horizontal gridlines + y-axis percentage labels, every 10%.
    steps = max(1, int(round(ymax / 0.1)))
    for s in range(steps + 1):
        val = s * 0.1
        y = Y(val)
        p.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - R}" y2="{y:.1f}" '
                 f'stroke="#E4E8ED" stroke-width="1"/>')
        p.append(f'<text x="{L - 8}" y="{y + 3.5:.1f}" text-anchor="end" '
                 f'class="axl">{int(round(val * 100))}%</text>')

    # X-axis run labels.
    for i, lab in enumerate(labels):
        p.append(f'<text x="{X(i):.1f}" y="{H - B + 22:.1f}" '
                 f'text-anchor="middle" class="axl">{html.escape(lab)}</text>')

    single = n <= 1
    if single:
        # A forward guide so a lone point reads as "the series starts here"
        # rather than a broken chart.
        gy = Y(series[0]["vals"][0]) if series and series[0]["vals"] else Y(0)
        p.append(f'<line x1="{X(0):.1f}" y1="{gy:.1f}" x2="{W - R}" y2="{gy:.1f}" '
                 f'stroke="{SLATE}" stroke-width="1.5" stroke-dasharray="2 5"/>')
        p.append(f'<text x="{W - R:.1f}" y="{gy - 8:.1f}" text-anchor="end" '
                 f'class="axl" fill="{SLATE}">future weeks &#8594;</text>')

    for s in series:
        color, width = s["color"], s["width"]
        vals = s["vals"]
        if not single:
            pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
            dash = ' stroke-dasharray="5 4"' if s["dash"] else ""
            p.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                     f'stroke-width="{width}" stroke-linejoin="round" '
                     f'stroke-linecap="round"{dash}/>')
        for i, v in enumerate(vals):
            r = 3.6 if s["color"] == BRAND else 2.8
            p.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="{r}" '
                     f'fill="{color}"/>')

    body = "\n    ".join(p)
    return (f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" '
            f'role="img" aria-label="Visibility trend across weekly runs">\n'
            f'    {body}\n  </svg>')


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

def _headline(meta: dict, metrics: list[EntityMetrics]) -> str:
    short = html.escape(meta["brand_short"])
    cat = html.escape(meta["category"])
    brand = _brand_metrics(metrics)
    if brand and metrics and metrics[0].is_brand:
        return (f'Ask AI about {cat}, and <span class="hl">{short}</span> '
                f'comes up more than anyone else.')
    if brand:
        rank = [m.entity for m in metrics].index(brand.entity) + 1
        return (f'When people ask AI about {cat}, '
                f'<span class="hl">{short}</span> currently ranks #{rank} '
                f'for how often it comes up.')
    return f'How <span class="hl">{short}</span> shows up when people ask AI about {cat}.'


def _readout(num: str, lbl: str, note: str) -> str:
    return (f'<div class="readout"><div class="num">{num}</div>'
            f'<div class="lbl">{html.escape(lbl)}</div>'
            f'<div class="note">{html.escape(note)}</div></div>')


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _mention_bars(metrics: list[EntityMetrics]) -> str:
    rows = []
    for i, m in enumerate(metrics):
        cls = "row brand" if m.is_brand else "row"
        delay = 0.05 + i * 0.07
        w = round(m.mention_rate * 100)
        rows.append(
            f'<div class="{cls}"><span class="name">{html.escape(m.entity)}</span>'
            f'<div class="track"><div class="fill" style="width:{w}%;'
            f'animation-delay:{delay:.2f}s"></div></div>'
            f'<span class="val">{_pct(m.mention_rate)}</span></div>')
    return "\n      ".join(rows)


def _sentiment_card(brand: EntityMetrics | None) -> str:
    sb = brand.sentiment_breakdown if brand else {}
    pos = sb.get("positive", 0)
    neu = sb.get("neutral", 0)
    neg = sb.get("negative", 0)
    total = pos + neu + neg
    if total == 0:
        inner = ('<p class="sub">No sentiment classified yet — enable sentiment '
                 'in config to populate this.</p>')
        return (f'<div class="card"><h3>How AI talks about us</h3>{inner}</div>')

    def w(x: int) -> float:
        return round(x / total * 100, 1)

    segs = []
    if pos:
        segs.append(f'<span style="width:{w(pos)}%;background:var(--pos)"></span>')
    if neu:
        segs.append(f'<span style="width:{w(neu)}%;background:var(--neu)"></span>')
    if neg:
        segs.append(f'<span style="width:{w(neg)}%;background:var(--neg)"></span>')
    return (
        '<div class="card">\n'
        '        <h3>How AI talks about us</h3>\n'
        f'        <p class="sub">Tone of every brand mention · {total} total</p>\n'
        f'        <div class="sent-bar">{"".join(segs)}</div>\n'
        '        <div class="sent-key">\n'
        f'          <span><i style="background:var(--pos)"></i>Positive <b>{pos}</b></span>\n'
        f'          <span><i style="background:var(--neu)"></i>Neutral <b>{neu}</b></span>\n'
        f'          <span><i style="background:var(--neg)"></i>Negative <b>{neg}</b></span>\n'
        '        </div>\n'
        '      </div>')


def _citation_card(metrics: list[EntityMetrics]) -> str:
    by_cite = sorted(metrics, key=lambda m: m.citation_rate, reverse=True)
    scale = by_cite[0].citation_rate if by_cite and by_cite[0].citation_rate > 0 else 1.0
    top = by_cite[:4]
    rest = by_cite[4:]
    rows = []
    for m in top:
        cls = "cite-row brand" if m.is_brand else "cite-row"
        w = round(m.citation_rate / scale * 100)
        rows.append(
            f'<div class="{cls}"><span class="cn">{html.escape(m.entity)}</span>'
            f'<div class="cite-track"><div class="cite-fill" style="width:{w}%">'
            f'</div></div><span class="cite-val">{_pct(m.citation_rate)}</span></div>')
    if rest:
        rmax = max(m.citation_rate for m in rest)
        label = f"≤{max(1, math.ceil(rmax * 100))}%" if rmax > 0 else "0%"
        w = max(2, round(rmax / scale * 100)) if rmax > 0 else 0
        rows.append(
            f'<div class="cite-row"><span class="cn">Everyone else</span>'
            f'<div class="cite-track"><div class="cite-fill" style="width:{w}%">'
            f'</div></div><span class="cite-val">{label}</span></div>')
    return (
        '<div class="card">\n'
        '        <h3>Who AI actually cites</h3>\n'
        '        <p class="sub">Answers linking the school\'s own site</p>\n'
        f'        {"".join(chr(10) + "        " + r for r in rows)}\n'
        '      </div>')


def _trend_section(series: list[dict], labels: list[str], n_runs: int) -> str:
    svg = _svg_trend(series, labels)
    legend = []
    for s in series:
        style = (f'border-top:3px {"dashed" if s["dash"] else "solid"} {s["color"]}')
        legend.append(f'<span><i style="{style}"></i>{html.escape(s["name"])}</span>')
    legend_html = "\n      ".join(legend)

    if n_runs <= 1:
        cap = "1 of an accumulating series"
        note = ('<p class="note1"><b>Week 1 of an accumulating series</b> — the '
                'trend fills in with each weekly run. Today shows a single point '
                'per line; after a few weeks these become lines you can read for '
                'whether visibility is climbing, holding, or slipping.</p>')
    else:
        cap = f"{n_runs} weekly runs"
        note = ""
    return (
        '  <section>\n'
        '    <div class="sec-head">\n'
        '      <h2>The trend</h2>\n'
        f'      <span class="cap">{html.escape(cap)}</span>\n'
        '    </div>\n'
        '    <div class="chart">\n'
        f'      {svg}\n'
        f'      <div class="legend">\n      {legend_html}\n      </div>\n'
        f'      {note}\n'
        '    </div>\n'
        '  </section>')


def render(db_path: str, config_path: str = "config.yaml") -> str:
    meta = _load_meta(config_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        runs = _run_timestamps(conn)
    except sqlite3.OperationalError:
        runs = []

    if not runs:
        conn.close()
        return _empty_page(meta)

    # Per-run metrics for the whole time series.
    per_run = [compute(db_path, since=ts, until=ts) for ts in runs]
    latest = per_run[-1]
    latest_info = _run_info(conn, runs[-1])
    conn.close()

    brand = _brand_metrics(latest)
    n_runs = len(runs)

    # --- trend series --------------------------------------------------------
    # Top competitors chosen by share of voice in the latest run.
    competitors = [m for m in latest if not m.is_brand]
    top_comp = sorted(competitors, key=lambda m: m.share_of_voice,
                      reverse=True)[:3]
    top_names = [m.entity for m in top_comp]

    def vals_for(entity: str, field: str) -> list[float]:
        out = []
        for run in per_run:
            m = next((x for x in run if x.entity == entity), None)
            out.append(getattr(m, field) if m else 0.0)
        return out

    brand_name = brand.entity if brand else meta["brand_name"]
    series: list[dict] = [
        {"name": f"{meta['brand_short']} mention rate", "color": BRAND,
         "dash": False, "width": 2.6, "vals": vals_for(brand_name, "mention_rate")},
        {"name": f"{meta['brand_short']} share of voice", "color": BRAND,
         "dash": True, "width": 2.2, "vals": vals_for(brand_name, "share_of_voice")},
    ]
    for i, name in enumerate(top_names):
        series.append({
            "name": f"{name} SoV",
            "color": COMPETITOR_COLORS[i % len(COMPETITOR_COLORS)],
            "dash": False, "width": 1.7, "vals": vals_for(name, "share_of_voice"),
        })

    labels = [f"wk {i + 1}" for i in range(n_runs)]

    # --- header facts --------------------------------------------------------
    engine_label = _engine_label(latest_info["engines"])
    rpp = latest_info["runs_per_prompt"]
    run_date = _fmt_date(runs[-1])

    week_word = "Week 1 baseline" if n_runs == 1 else f"Latest of {n_runs} runs"

    headline = _headline(meta, latest)

    mr = _pct(brand.mention_rate) if brand else "—"
    sov = _pct(brand.share_of_voice) if brand else "—"
    cite = _pct(brand.citation_rate) if brand else "—"

    domain = meta["domain"] or "the brand's site"
    short = html.escape(meta["brand_short"])

    readouts = "\n    ".join([
        _readout(mr, "Mention rate", f"Answers that named {meta['brand_short']}"),
        _readout(sov, "Share of voice", f"{meta['brand_short']} vs. all rivals combined"),
        _readout(cite, "Citation rate", f"Answers linking {domain}"),
    ])

    lede = (f"A read on how {html.escape(meta['brand_name'])} shows up when people "
            f"ask {html.escape(engine_label)} about {html.escape(meta['category'])} "
            f"— measured across a fixed set of {latest_info['prompts']} questions, "
            f"{rpp}× each, with no personalization from any of our accounts.")

    trend = _trend_section(series, labels, n_runs)
    mention_bars = _mention_bars(latest)
    sentiment = _sentiment_card(brand)
    citation = _citation_card(latest)

    return _PAGE.format(
        title=f"AEO Monitor — {html.escape(meta['brand_name'])}",
        brand_footer=html.escape(meta["brand_name"]),
        styles=_STYLES,
        week_word=html.escape(week_word),
        headline=headline,
        lede=lede,
        engine_label=html.escape(engine_label),
        prompts=latest_info["prompts"],
        responses=latest_info["responses"],
        run_date=html.escape(run_date),
        readouts=readouts,
        n_answers=latest_info["responses"],
        mention_bars=mention_bars,
        sentiment=sentiment,
        citation=citation,
        trend=trend,
        rpp=rpp,
        engine_first=html.escape(_engine_label(latest_info["engines"][:1])),
    )


def _empty_page(meta: dict) -> str:
    return _PAGE.format(
        title=f"AEO Monitor — {html.escape(meta['brand_name'])}",
        brand_footer=html.escape(meta["brand_name"]),
        styles=_STYLES,
        week_word="No data yet",
        headline=f'No runs recorded yet for <span class="hl">'
                 f'{html.escape(meta["brand_short"])}</span>.',
        lede="Run the pipeline (<code>python3 -m aeo.cli run</code>) to record "
             "the first weekly measurement, then regenerate this page.",
        engine_label="—", prompts=0, responses=0, run_date="—",
        readouts="\n    ".join([_readout("—", "Mention rate", "Awaiting first run"),
                                _readout("—", "Share of voice", "Awaiting first run"),
                                _readout("—", "Citation rate", "Awaiting first run")]),
        n_answers=0,
        mention_bars='<div class="row"><span class="name">No data</span>'
                     '<div class="track"><div class="fill" style="width:0%"></div>'
                     '</div><span class="val">—</span></div>',
        sentiment='<div class="card"><h3>How AI talks about us</h3>'
                  '<p class="sub">No data yet.</p></div>',
        citation='<div class="card"><h3>Who AI actually cites</h3>'
                 '<p class="sub">No data yet.</p></div>',
        trend='  <section><div class="sec-head"><h2>The trend</h2></div>'
              '<div class="chart"><p class="note1">The trend chart appears once '
              'the first run is recorded.</p></div></section>',
        rpp=0, engine_first="—",
    )


def generate(db_path: str, out_path: str, config_path: str = "config.yaml") -> str:
    html_str = render(db_path, config_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    return out_path


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="aeo.report_html",
                                description="Generate the static AEO dashboard.")
    p.add_argument("--db", default="aeo.db", help="path to the SQLite database")
    p.add_argument("--out", default="public/index.html",
                   help="output HTML path")
    p.add_argument("--config", default="config.yaml",
                   help="config.yaml for brand labels (never metrics)")
    args = p.parse_args(argv)
    out = generate(args.db, args.out, args.config)
    print(f"Wrote {out}")


# --------------------------------------------------------------------------- #
# Template + styles (palette/type/layout mirror aeo-week1-snapshot.html)
# --------------------------------------------------------------------------- #

_STYLES = """
  :root{
    --paper:#F6F7F9;
    --panel:#FFFFFF;
    --ink:#0F172A;
    --ink-soft:#475569;
    --ink-faint:#94A3B8;
    --brand:#4F46E5;
    --brand-soft:#E5E4FB;
    --slate:#B4BECD;
    --slate-track:#EBEEF2;
    --pos:#4F46E5;
    --neu:#C7CDD6;
    --neg:#B45309;
    --line:#E4E8ED;
    --display:"Space Grotesk",system-ui,sans-serif;
    --body:"Inter",system-ui,sans-serif;
    --mono:"IBM Plex Mono",ui-monospace,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--paper);color:var(--ink);font-family:var(--body);
    line-height:1.5;-webkit-font-smoothing:antialiased;padding:clamp(20px,5vw,64px)}
  .wrap{max-width:920px;margin:0 auto}
  code{font-family:var(--mono);font-size:.9em;background:var(--slate-track);
    padding:1px 6px;border-radius:5px}

  .eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--brand);font-weight:600;
    display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .eyebrow .dot{width:6px;height:6px;border-radius:50%;background:var(--brand);display:inline-block}
  .eyebrow .muted{color:var(--ink-faint)}
  h1{font-family:var(--display);font-weight:700;font-size:clamp(28px,5.2vw,46px);
    line-height:1.04;letter-spacing:-.02em;margin:18px 0 14px;max-width:18ch}
  h1 .hl{color:var(--brand)}
  .lede{font-size:clamp(15px,2vw,17px);color:var(--ink-soft);max-width:60ch}
  .meta{font-family:var(--mono);font-size:12.5px;color:var(--ink-faint);
    margin-top:16px;padding-top:16px;border-top:1px solid var(--line);
    display:flex;gap:22px;flex-wrap:wrap}
  .meta b{color:var(--ink-soft);font-weight:500}

  .readouts{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:34px 0 8px}
  .readout{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 20px 18px}
  .readout .num{font-family:var(--display);font-weight:700;font-size:clamp(34px,6vw,52px);
    letter-spacing:-.03em;color:var(--brand);line-height:1}
  .readout .lbl{font-family:var(--mono);font-size:11.5px;letter-spacing:.08em;
    text-transform:uppercase;color:var(--ink-soft);margin-top:10px}
  .readout .note{font-size:12.5px;color:var(--ink-faint);margin-top:6px}

  section{margin-top:44px}
  .sec-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:18px}
  .sec-head h2{font-family:var(--display);font-weight:600;font-size:19px;letter-spacing:-.01em}
  .sec-head .cap{font-family:var(--mono);font-size:11.5px;color:var(--ink-faint);
    letter-spacing:.05em;text-transform:uppercase}

  .bars{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:12px 22px}
  .row{display:grid;grid-template-columns:150px 1fr 54px;align-items:center;gap:16px;
    padding:11px 0;border-bottom:1px solid var(--line)}
  .row:last-child{border-bottom:none}
  .row .name{font-size:13.5px;font-weight:500;color:var(--ink-soft);white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis}
  .row.brand .name{color:var(--ink);font-weight:600}
  .track{background:var(--slate-track);height:16px;border-radius:8px;overflow:hidden}
  .fill{height:100%;border-radius:8px;background:var(--slate);transform-origin:left;
    transform:scaleX(0);animation:grow .9s cubic-bezier(.2,.7,.2,1) forwards}
  .row.brand .fill{background:var(--brand)}
  .val{font-family:var(--mono);font-size:13px;font-weight:500;text-align:right;color:var(--ink-soft)}
  .row.brand .val{color:var(--brand);font-weight:600}
  @keyframes grow{to{transform:scaleX(1)}}
  @media (prefers-reduced-motion:reduce){.fill{animation:none;transform:scaleX(1)}}

  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px}
  .card h3{font-family:var(--display);font-size:15px;font-weight:600;margin-bottom:4px}
  .card .sub{font-size:12.5px;color:var(--ink-faint);margin-bottom:16px}
  .sent-bar{display:flex;height:20px;border-radius:6px;overflow:hidden;margin-bottom:12px}
  .sent-bar span{display:block;height:100%}
  .sent-key{display:flex;gap:18px;font-size:12.5px;color:var(--ink-soft);flex-wrap:wrap}
  .sent-key i{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:6px;vertical-align:baseline}
  .sent-key b{font-family:var(--mono);font-weight:600;color:var(--ink)}
  .cite-row{display:grid;grid-template-columns:120px 1fr 40px;align-items:center;gap:12px;margin:9px 0}
  .cite-row .cn{font-size:12.5px;color:var(--ink-soft)}
  .cite-row.brand .cn{color:var(--ink);font-weight:600}
  .cite-track{background:var(--slate-track);height:11px;border-radius:6px;overflow:hidden}
  .cite-fill{height:100%;background:var(--slate);border-radius:6px}
  .cite-row.brand .cite-fill{background:var(--brand)}
  .cite-val{font-family:var(--mono);font-size:12px;text-align:right;color:var(--ink-soft)}
  .cite-row.brand .cite-val{color:var(--brand);font-weight:600}

  .chart{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px 22px 16px}
  .chart svg{display:block;width:100%;height:auto}
  .chart .axl{font-family:var(--mono);font-size:10.5px;fill:var(--ink-faint)}
  .chart .legend{display:flex;gap:20px;flex-wrap:wrap;margin-top:16px;
    font-size:12.5px;color:var(--ink-soft);font-family:var(--mono)}
  .chart .legend i{width:16px;display:inline-block;margin-right:8px;vertical-align:middle}
  .chart .note1{margin-top:16px;padding-top:14px;border-top:1px solid var(--line);
    font-size:13.5px;color:var(--ink-soft);max-width:64ch}
  .chart .note1 b{color:var(--brand);font-weight:600}

  .frame{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}
  .frame .col{padding:20px 22px;border-radius:14px}
  .frame .is{background:var(--brand-soft)}
  .frame .isnt{background:#fff;border:1px solid var(--line)}
  .frame h4{font-family:var(--mono);font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:12px}
  .frame .is h4{color:var(--brand)}
  .frame .isnt h4{color:var(--ink-faint)}
  .frame ul{list-style:none;display:flex;flex-direction:column;gap:9px}
  .frame li{font-size:13.5px;color:var(--ink-soft);padding-left:18px;position:relative}
  .frame li::before{content:"";position:absolute;left:0;top:8px;width:6px;height:6px;
    border-radius:50%;background:var(--brand)}
  .frame .isnt li::before{background:var(--ink-faint)}

  footer{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
    font-family:var(--mono);font-size:11.5px;color:var(--ink-faint);
    display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}

  @media (max-width:640px){
    .readouts{grid-template-columns:1fr}
    .grid2,.frame{grid-template-columns:1fr}
    .row{grid-template-columns:110px 1fr 46px;gap:10px}
  }
"""

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{styles}</style>
</head>
<body>
<div class="wrap">

  <header>
    <div class="eyebrow">
      <span class="dot"></span> AEO Monitor
      <span class="muted">— {week_word}</span>
    </div>
    <h1>{headline}</h1>
    <p class="lede">{lede}</p>
    <div class="meta">
      <span><b>Engine:</b> {engine_label}</span>
      <span><b>Questions:</b> {prompts}</span>
      <span><b>Responses:</b> {responses}</span>
      <span><b>Run:</b> {run_date}</span>
    </div>
  </header>

  <div class="readouts">
    {readouts}
  </div>

  <section>
    <div class="sec-head">
      <h2>How often each school appears</h2>
      <span class="cap">Mention rate · {n_answers} answers</span>
    </div>
    <div class="bars">
      {mention_bars}
    </div>
  </section>

  <section>
    <div class="grid2">
      {sentiment}
      {citation}
    </div>
  </section>

{trend}

  <section>
    <div class="sec-head">
      <h2>How to read this honestly</h2>
    </div>
    <div class="frame">
      <div class="col is">
        <h4>What it is</h4>
        <ul>
          <li>Clean-room queries — no personalization from our accounts or search history</li>
          <li>Each question asked {rpp}×, reported as rates, not one-off answers</li>
          <li>A real, defensible baseline for our standing in {engine_first}</li>
        </ul>
      </div>
      <div class="col isnt">
        <h4>What it isn't (yet)</h4>
        <ul>
          <li>Engine coverage is limited to what's configured — other AI engines can differ</li>
          <li>A trend line only sharpens as more weekly runs accumulate</li>
          <li>Not any single prospect's exact view; results vary by search and region</li>
        </ul>
      </div>
    </div>
  </section>

  <footer>
    <span>AEO Monitor · {brand_footer}</span>
    <span>{week_word} · {engine_label} · {run_date}</span>
  </footer>

</div>
</body>
</html>
"""


if __name__ == "__main__":
    main()
