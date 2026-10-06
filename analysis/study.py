"""Build the study results: a JSON file of every number, and a self-contained
static page at public/study/index.html.

    python3 analysis/study.py                      # uses config.study.yaml
    python3 analysis/study.py --config config.yaml # or the monitor's data
    python3 analysis/study.py --exclude-truncated

Charts are inline SVG generated here rather than matplotlib images, so the
page is one file with no assets, scales to any screen, and inherits the page's
own colours. Vercel serves public/ as static files with no build step, so this
drops in and deploys with nothing to configure.

The page leads with the prompt-composition effect: how often a brand is named
depends on whether, and how, the prompt asks about it. Brand figures follow.

Design note: the brand chart draws confidence INTERVALS as range bars, not
bars. Bars imply a precision this sample size does not have, and the whole
point of the study is to be honest about that. The ranges are the argument.
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aeo.config import load_config                                  # noqa: E402
from aeo.stats import (_filters, brand_stats, by_intent,  # noqa: E402
                       cross_model_agreement, engine_summary,
                       significance_matrix, top_domains)

# =============================================================================
# HAND-WRITTEN PAGE PROSE. NOT GENERATED FROM THE DATA.
#
# These three strings are typed by the author. They quote specific figures
# (shares, counts) from the run with Gemini excluded: 270 answers across
# Claude, ChatGPT and Perplexity. If the data, the engine set or the source
# classifications change, re-check every number here by hand and edit it.
# Nothing will update them.
# =============================================================================
HEADLINE_CLAIM = (
    "When a prompt names a brand, the answer names that brand. In this study it "
    "happened in every case. The further a question gets from naming anyone, "
    "the less often any brand appears at all.")

INTENT_PARA = (
    "Commercial questions get answered with a list of schools: almost every "
    "commercial answer names at least one of the ten brands. Informational "
    "questions get answered with an essay: most informational answers name no "
    "bootcamp at all. Asking whether bootcamps are worth it does not make the "
    "engine recommend anyone, so nobody is visible.")

FORMULA_PARA = (
    "So a visibility score is roughly the share of prompts that name the brand, "
    "times 100%, plus the share that don't, times the brand's actual rate on "
    "open questions. The first term is set by whoever wrote the prompt list. "
    "Only the second term is the brand.")

# The original monitor's database was collected for a former employer and is
# not in this repository. These figures are typed from it by hand: the last of
# its six weekly runs (31 Aug 2026, Claude only, 30 prompts, 147 answers), and
# all six runs for the brand-named total.
MONITOR = {
    "named_prompts": 12, "total_prompts": 30,
    "named_answers": 57, "named_hits": 57,
    "other_answers": 90, "other_hits": 33,
    "total_answers": 147, "total_hits": 90,
    "all_runs_named": 482,
}

MONITOR_PARA = (
    "This study exists because of that dashboard. It was a weekly monitor built "
    "for one bootcamp's marketing team, and it reported that the brand appeared "
    "in 61% of AI answers about its category. Twelve of its thirty prompts "
    "named the brand. Across six weekly runs, every one of the 482 answers to "
    "those prompts named it. On the other eighteen prompts, the same engine in "
    "the same week named it in 37% of answers. Of the 61 points on the "
    "dashboard, 39 came from the prompt list.")

MONITOR_CONTEXT = (
    "The monitor's 37% and this study's figure for the same brand are not the "
    "same measurement: one engine in August on prompts written to watch one "
    "brand, against three engines in October on prompts written to survey a "
    "category. Both sit a long way below 61.")

CITATION_NOTE = (
    "These are the sources the engines' search step returned, not the sources "
    "the answers drew on. Perplexity and Claude return everything their search "
    "retrieved: one answer here retrieves a Nucamp page and then talks entirely "
    "about a different school. Read this as what the engines looked at, not "
    "what persuaded them.")

DISCARDED = [
    ("Content farms out-cite the brands' own sites.",
     "A manual check of five flagged domains found a program-matching search "
     "tool, a paid tutoring service and a legitimate developer academy. The "
     "classification was a judgement, and it was wrong often enough that the "
     "number meant nothing."),
    ("Reddit barely registers.",
     "Community sources were under 1% of retrieved sources. Reddit's citation "
     "share in ChatGPT had already collapsed in August 2026, and that was "
     "widely covered. Not a finding."),
    ("API results differ from what users see, so commercial tools measure the "
     "wrong thing.",
     "Backwards. Commercial tools collect from the browser to avoid exactly "
     "that discrepancy. This study runs on APIs, so it is this study's "
     "limitation."),
    ("Perplexity grounds every answer; Gemini grounds almost none.",
     "Perplexity was given an explicit search instruction during the run and "
     "Gemini was not. The comparison was confounded by configuration."),
    ("Some brands are cited far more often than they are named.",
     "Nucamp's site appeared in the returned sources of 101 answers while its "
     "name appeared in about half as many. That is a fact about what "
     "\"citation\" means for these engines (retrieved, not used), not about "
     "Nucamp."),
]
# =============================================================================

# --- palette ---------------------------------------------------------------
# Cool pale ground and deep pine rather than the cream-and-terracotta that
# every generated report page arrives in. Teal carries measured data; plum
# marks the categories the study is flagging; sand is neutral volume.
C = {
    "ground": "#F1F3F1",
    "ink": "#16302B",
    "teal": "#2E6E68",
    "teal_soft": "#8FB5B1",
    "plum": "#6B3352",
    "sand": "#C9BFA9",
    "grey": "#75817D",
    "rule": "#D4DAD7",
}

SOURCE_LABELS = {
    "review_aggregator": "Review sites and directories",
    "content_farm": "Content farms",
    "owned": "Brands' own websites",
    "competitor_content": "Other vendors' content",
    "editorial": "News and editorial",
    "community": "Reddit, forums, social",
    "educational": "Universities and schools",
    "commercial_service": "Paid essay services",
    "government": "Government and statistics",
    "offtopic_commercial": "Unrelated product blogs",
    "industry_body": "Industry standards body",
    "other": "Unclassified",
    "infrastructure": "CDN and asset hosts",
    "video": "Video",
}


ENGINE_NAMES = {"anthropic": "Claude", "openai": "ChatGPT",
                "perplexity": "Perplexity", "gemini": "Gemini"}


def esc(s) -> str:
    return html.escape(str(s))


# --- charts ----------------------------------------------------------------

def chart_intervals(rows, width=760, row_h=34) -> str:
    """Horizontal range bars: each brand's 95% confidence interval, with a
    tick at the observed rate. Overlapping ranges are visibly overlapping,
    which is the honest way to show a ranking that isn't one."""
    if not rows:
        return ""
    pad_l, pad_r, pad_t, pad_b = 168, 56, 30, 34
    plot_w = width - pad_l - pad_r
    height = pad_t + len(rows) * row_h + pad_b
    hi = max(r["mention_ci"][1] for r in rows)
    scale_max = min(1.0, (int(hi * 100 / 10) + 1) / 10)
    x = lambda v: pad_l + (v / scale_max) * plot_w          # noqa: E731

    p = [f'<svg viewBox="0 0 {width} {height}" role="img" '
         f'aria-label="Mention rate with 95% confidence intervals" '
         f'xmlns="http://www.w3.org/2000/svg">']
    step = 0.1 if scale_max <= 0.6 else 0.2
    v = 0.0
    while v <= scale_max + 1e-9:
        gx = x(v)
        p.append(f'<line x1="{gx:.1f}" y1="{pad_t - 8}" x2="{gx:.1f}" '
                 f'y2="{height - pad_b}" stroke="{C["rule"]}" stroke-width="1"/>')
        p.append(f'<text x="{gx:.1f}" y="{height - pad_b + 18}" '
                 f'text-anchor="middle" font-size="12" fill="{C["grey"]}">'
                 f'{v:.0%}</text>')
        v += step

    for i, r in enumerate(rows):
        y = pad_t + i * row_h + row_h / 2
        lo, up = r["mention_ci"]
        p.append(f'<text x="{pad_l - 14}" y="{y + 4}" text-anchor="end" '
                 f'font-size="14" fill="{C["ink"]}">{esc(r["brand"])}</text>')
        p.append(f'<line x1="{x(lo):.1f}" y1="{y:.1f}" x2="{x(up):.1f}" '
                 f'y2="{y:.1f}" stroke="{C["teal_soft"]}" stroke-width="10" '
                 f'stroke-linecap="round"/>')
        p.append(f'<line x1="{x(r["mention_rate"]):.1f}" y1="{y - 9:.1f}" '
                 f'x2="{x(r["mention_rate"]):.1f}" y2="{y + 9:.1f}" '
                 f'stroke="{C["teal"]}" stroke-width="3"/>')
        p.append(f'<text x="{x(up) + 10:.1f}" y="{y + 4:.1f}" font-size="13" '
                 f'fill="{C["grey"]}">{r["mention_rate"]:.0%}</text>')
    p.append("</svg>")
    return "".join(p)


def chart_intent(rows, width=760, row_h=58) -> str:
    """Pooled mention rate by prompt type, as bars with interval whiskers.
    Bars are right here: these are large pooled samples, and the point is the
    size of the steps between rows."""
    if not rows:
        return ""
    pad_l, pad_r, pad_t, pad_b = 190, 96, 14, 30
    plot_w = width - pad_l - pad_r
    height = pad_t + len(rows) * row_h + pad_b
    x = lambda v: pad_l + v * plot_w                         # noqa: E731
    p = [f'<svg viewBox="0 0 {width} {height}" role="img" '
         f'aria-label="Share of answers naming a given brand, by prompt type" '
         f'xmlns="http://www.w3.org/2000/svg">']
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        gx = x(v)
        p.append(f'<line x1="{gx:.1f}" y1="{pad_t}" x2="{gx:.1f}" '
                 f'y2="{height - pad_b}" stroke="{C["rule"]}" stroke-width="1"/>')
        p.append(f'<text x="{gx:.1f}" y="{height - pad_b + 18}" '
                 f'text-anchor="middle" font-size="12" fill="{C["grey"]}">'
                 f'{v:.0%}</text>')
    for i, r in enumerate(rows):
        y = pad_t + i * row_h + row_h / 2
        lo, up = r["ci"]
        p.append(f'<text x="{pad_l - 14}" y="{y - 2}" text-anchor="end" '
                 f'font-size="15" font-weight="600" fill="{C["ink"]}">'
                 f'{esc(r["label"])}</text>')
        p.append(f'<text x="{pad_l - 14}" y="{y + 15}" text-anchor="end" '
                 f'font-size="12" fill="{C["grey"]}">{r["k"]:,} of {r["n"]:,}'
                 f'</text>')
        p.append(f'<rect x="{pad_l}" y="{y - 10:.1f}" '
                 f'width="{max(x(r["rate"]) - pad_l, 2):.1f}" height="20" '
                 f'rx="3" fill="{C["teal"]}"><title>{esc(r["label"])}: '
                 f'{r["rate"]:.1%} ({r["k"]} of {r["n"]}), 95% interval '
                 f'{lo:.1%} to {up:.1%}</title></rect>')
        p.append(f'<line x1="{x(lo):.1f}" y1="{y:.1f}" x2="{x(up):.1f}" '
                 f'y2="{y:.1f}" stroke="{C["ink"]}" stroke-width="1.5"/>')
        for xx in (lo, up):
            p.append(f'<line x1="{x(xx):.1f}" y1="{y - 6:.1f}" x2="{x(xx):.1f}" '
                     f'y2="{y + 6:.1f}" stroke="{C["ink"]}" stroke-width="1.5"/>')
        label = "100%" if r["k"] == r["n"] else f'{r["rate"]:.1%}'
        p.append(f'<text x="{x(up) + 10:.1f}" y="{y + 5:.1f}" font-size="15" '
                 f'font-weight="600" fill="{C["ink"]}">{label}</text>')
    p.append("</svg>")
    return "".join(p)


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return ((c - h) / d, (c + h) / d)


INTENT_LABELS = {"named": "Names the brand", "commercial": "Commercial",
                 "transactional": "Transactional",
                 "informational": "Informational"}
INTENT_EXAMPLES = {"named": "Hack Reactor vs App Academy?",
                   "commercial": "Best bootcamp for a career changer?",
                   "transactional": "How do I pay for a bootcamp?",
                   "informational": "Are bootcamps still worth it in 2026?"}


def pooled_by_intent(db: str, cfg, **kw) -> list[dict]:
    """Every (brand, answer) pair, bucketed by how the prompt relates to that
    brand: the prompt names it, or the prompt's intent. On comparative prompts
    the brands not named are left out, since 'comparative about someone else'
    is not a category any reader would recognise."""
    import re
    ents = cfg.all_entities
    named = {}
    for pr in cfg.prompts:
        named[pr.id] = {
            e.name for e in ents
            if any(re.search(rf"\b{re.escape(a)}\b", pr.text, re.I)
                   for a in (e.aliases or [e.name]))}
    intents = {pr.id: pr.intent for pr in cfg.prompts}
    clause, params = _filters(**kw)
    conn = sqlite3.connect(db)
    q = (f"SELECT r.prompt_id, m.entity, (m.count > 0) FROM mentions m "
         f"JOIN responses r ON r.id = m.response_id WHERE {clause}")
    tally: dict[str, list[int]] = {}
    for pid, ent, hit in conn.execute(q, params):
        if ent in named.get(pid, set()):
            bucket = "named"
        elif intents.get(pid) == "comparative":
            continue
        else:
            bucket = intents.get(pid, "unclassified")
        t = tally.setdefault(bucket, [0, 0])
        t[0] += int(hit); t[1] += 1
    conn.close()
    out = []
    for key in ("named", "commercial", "transactional", "informational"):
        if key in tally:
            k, n = tally[key]
            out.append({"key": key, "label": INTENT_LABELS[key],
                        "example": INTENT_EXAMPLES[key], "k": k, "n": n,
                        "rate": k / n if n else 0.0, "ci": list(_wilson(k, n))})
    return out


# --- page ------------------------------------------------------------------

def build_html(d: dict) -> str:
    rows = d["brands"]
    disputed = [s for s in d["significance"] if not s["distinguishable"]][:6]
    pooled = d["pooled_intent"]
    m = d["monitor"]

    pooled_rows = "".join(
        f'<tr><td>{esc(r["label"])}<br><span class="ex">"{esc(r["example"])}"'
        f'</span></td><td class="n">{r["rate"]:.1%}</td>'
        f'<td class="n">{r["k"]:,} of {r["n"]:,}</td>'
        f'<td class="t">{r["ci"][0]:.1%} to {r["ci"][1]:.1%}</td></tr>'
        for r in pooled)

    def mrow(label, prompts, n, k, strong=False):
        tag = "b" if strong else "span"
        return (f'<tr><td><{tag}>{label}</{tag}></td><td class="n">{prompts}</td>'
                f'<td class="n">{n}</td><td class="n"><{tag}>{k} '
                f'({k / n:.0%})</{tag}></td></tr>')
    monitor_rows = (
        mrow("Prompts naming the brand", m["named_prompts"], m["named_answers"],
             m["named_hits"])
        + mrow("Prompts that don't", m["total_prompts"] - m["named_prompts"],
               m["other_answers"], m["other_hits"])
        + mrow("As reported", m["total_prompts"], m["total_answers"],
               m["total_hits"], strong=True))

    discarded = "".join(f'<li><b>{esc(t)}</b> {esc(w)}</li>'
                        for t, w in d["discarded"])

    domain_rows = "".join(
        f'<tr><td>{esc(x["domain"])}</td>'
        f'<td class="n">{x["n"]}</td>'
        f'<td class="t">'
        f'{esc(SOURCE_LABELS.get(x["source_type"] or "other", "Unclassified"))}'
        f'</td></tr>' for x in d["top_domains"][:15])

    intent_rows = ""
    for intent, brands in d["by_intent"].items():
        lead = ", ".join(f'{esc(b["brand"])} {b["mention_rate"]:.0%}'
                         for b in brands[:3])
        intent_rows += (f'<tr><td>{esc(intent)}</td>'
                        f'<td class="n">{brands[0]["n"] if brands else 0}</td>'
                        f'<td class="t">{lead}</td></tr>')

    overlaps = "".join(
        f'<li>{esc(s["a"])} ({s["a_rate"]:.0%}) and {esc(s["b"])} '
        f'({s["b_rate"]:.0%})</li>' for s in disputed)

    separate = ""
    for e in d.get("separate_engines", []):
        name = ENGINE_NAMES.get(e["engine"], e["engine"])
        separate += (
            f'<h2>{esc(name)}, reported separately</h2>'
            f'<p>{esc(name)} is left out of every figure above. Its answers '
            f'stay in the database and are summarised here.</p>'
            f'<table><tbody>'
            f'<tr><td>Answers</td><td class="n">{e["answers"]}</td></tr>'
            f'<tr><td>Answers that cited nothing</td>'
            f'<td class="n">{e["ungrounded"]}</td></tr>'
            f'<tr><td>Average citations per answer</td>'
            f'<td class="n">{e["avg_citations"]:.2f}</td></tr>'
            f'</tbody></table>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(d["title"])}</title>
<meta name="description" content="How prompt wording sets AI visibility
 scores: {d["n"]} answers about coding bootcamps across three answer engines.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,300;0,400;0,600;0,700;1,400&display=swap" rel="stylesheet">
<style>
  :root {{
    --ground:{C["ground"]}; --ink:{C["ink"]}; --teal:{C["teal"]};
    --plum:{C["plum"]}; --grey:{C["grey"]}; --rule:{C["rule"]};
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:var(--ground); color:var(--ink);
    font:400 18px/1.62 "Source Sans 3", ui-sans-serif, system-ui, sans-serif;
    -webkit-font-smoothing:antialiased;
  }}
  .wrap {{ max-width:860px; margin:0 auto; padding:0 24px 96px; }}
  p, li {{ max-width:64ch; }}
  header {{ padding:72px 0 8px; }}
  h1 {{
    font-size:clamp(34px,5.4vw,56px); line-height:1.06; font-weight:700;
    letter-spacing:-0.022em; margin:0 0 20px; max-width:19ch;
  }}
  .standfirst {{ font-size:21px; color:var(--grey); margin:0 0 28px; }}
  .meta {{
    font-size:15px; color:var(--grey); border-top:1px solid var(--rule);
    padding-top:14px;
  }}
  .meta span {{ margin-right:26px; white-space:nowrap; }}
  h2 {{
    font-size:27px; font-weight:600; letter-spacing:-0.012em;
    margin:64px 0 6px; max-width:24ch;
  }}
  h3 {{ font-size:19px; font-weight:600; margin:36px 0 4px; }}
  .sub {{ color:var(--grey); font-size:16px; margin:0 0 22px; }}
  figure {{ margin:26px 0 8px; }}
  svg {{ width:100%; height:auto; display:block; }}
  figcaption {{ font-size:15px; color:var(--grey); margin-top:12px; max-width:64ch; }}
  .legend {{
    list-style:none; padding:0; margin:18px 0 0; display:flex;
    flex-wrap:wrap; gap:6px 22px; font-size:15px; max-width:100%;
  }}
  .legend li {{ display:flex; align-items:center; gap:8px; }}
  .legend b {{ font-weight:600; color:var(--grey); }}
  .sw {{ width:13px; height:13px; border-radius:3px; display:inline-block; }}
  .key {{ display:flex; gap:24px; font-size:15px; color:var(--grey); margin:14px 0 0; }}
  .key i {{ width:11px; height:11px; border-radius:50%; display:inline-block;
            margin-right:7px; vertical-align:baseline; }}
  table {{ width:100%; border-collapse:collapse; margin:22px 0; font-size:16px; }}
  th, td {{ text-align:left; padding:9px 12px 9px 0; border-bottom:1px solid var(--rule); }}
  th {{ font-weight:600; font-size:14px; color:var(--grey); }}
  td.n, th.n {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; padding-left:16px; }}
  td.t {{ color:var(--grey); }}
  .pull {{
    border-left:3px solid var(--plum); padding:4px 0 4px 22px;
    margin:34px 0; font-size:22px; line-height:1.42; max-width:46ch;
  }}
  .caveat {{
    background:#fff; border:1px solid var(--rule); border-radius:6px;
    padding:26px 30px; margin:56px 0 0;
  }}
  .caveat h2 {{ margin-top:0; font-size:21px; }}
  .caveat li {{ font-size:16px; margin-bottom:9px; }}
  .ex {{ color:var(--grey); font-size:14px; font-style:italic; }}
  img {{ max-width:100%; height:auto; display:block; border:1px solid var(--rule);
         border-radius:6px; }}
  .discard li {{ margin-bottom:14px; }}
  footer {{
    margin-top:64px; padding-top:20px; border-top:1px solid var(--rule);
    font-size:15px; color:var(--grey);
  }}
  a {{ color:var(--teal); }}
  a:focus-visible, :focus-visible {{ outline:2px solid var(--teal); outline-offset:3px; }}
  @media (max-width:640px) {{
    body {{ font-size:17px; }}
    header {{ padding-top:44px; }}
    .meta span {{ display:block; margin:0 0 4px; }}
  }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>{esc(d["title"])}</h1>
  <p class="standfirst">{esc(d["standfirst"])}</p>
  <p class="meta">
    <span>Tonishqa Kaplish</span>
    <span>{d["n"]} answers</span>
    <span>{d["n_prompts"]} questions</span>
    <span>{esc(d["engines_label"])}</span>
    <span>{esc(d["window"])}</span>
  </p>
</header>

<h2>It depends how you ask</h2>
<p class="sub">Share of answers naming a given brand, by prompt type. Every
brand checked against every answer, pooled across all ten brands.</p>
<figure>
  {chart_intent(pooled)}
  <figcaption>Whiskers are 95% Wilson confidence intervals. No two rows
  overlap.</figcaption>
</figure>

<p class="pull">{esc(d["headline_claim"])}</p>

<table>
  <thead><tr><th>Prompt type</th><th class="n">Rate</th>
  <th class="n">Brand-answer pairs</th><th class="t">95% interval</th></tr></thead>
  <tbody>{pooled_rows}</tbody>
</table>

<p>{esc(d["intent_para"])}</p>
<p>{esc(d["formula_para"])}</p>

<h2>What that did to a real monitor</h2>
<figure>
  <img src="monitor-before.png" alt="The original monitor's dashboard, reporting a 61% mention rate for one brand across 147 Claude answers." loading="lazy">
  <figcaption>The dashboard that started this, as it was published in
  August 2026.</figcaption>
</figure>
<p>{esc(d["monitor_para"])}</p>
<table>
  <thead><tr><th>Monitor, final weekly run</th><th class="n">Prompts</th>
  <th class="n">Answers</th><th class="n">Brand named</th></tr></thead>
  <tbody>{monitor_rows}</tbody>
</table>
<p>{esc(d["monitor_context"])}</p>

<h2>How often each brand comes up</h2>
<p class="sub">Mention rate across every prompt, with its 95% confidence
interval.</p>
<figure>
  {chart_intervals(rows)}
  <figcaption>The bar is the range the true rate is very likely to fall in.
  The tick is what was measured. Where two bars overlap, the difference between
  those brands is not real at this sample size, however different the
  percentages look.</figcaption>
</figure>

{"<h3>Differences you cannot claim</h3><ul>" + overlaps + "</ul>" if overlaps else ""}

<h3>By prompt type, per brand</h3>
<table>
  <thead><tr><th>Question type</th><th class="n">Answers</th>
  <th class="t">Most mentioned</th></tr></thead>
  <tbody>{intent_rows}</tbody>
</table>

<h2>The fifteen most-retrieved websites</h2>
<p>{esc(d["citation_note"])}</p>
<table>
  <thead><tr><th>Domain</th><th class="n">Retrieved</th>
  <th class="t">Type</th></tr></thead>
  <tbody>{domain_rows}</tbody>
</table>

<h2>What was tested and thrown out</h2>
<p class="sub">Findings that looked publishable for a day or two and
weren't.</p>
<ul class="discard">{discarded}</ul>

{separate}
<div class="caveat">
  <h2>How this was measured, and what it can't tell you</h2>
  <ul>{"".join(f"<li>{esc(c)}</li>" for c in d["caveats"])}</ul>
</div>

<footer>
  <p>{esc(d["footer"])}<br><a href="https://github.com/tkaplish888-alt/ai-visibility-audit">Code and data on GitHub</a>. Generated {esc(d["generated"])}.</p>
</footer>

</div>
</body>
</html>
"""


def collect(db: str, cfg, exclude_truncated: bool = False,
            exclude_engines: list | None = None) -> dict:
    kw = {"exclude_truncated": exclude_truncated,
          "exclude_engines": exclude_engines or []}
    rows = [{
        "brand": b.brand, "n": b.n, "mentions": b.mentions,
        "mention_rate": b.mention_rate, "mention_ci": list(b.mention_ci),
        "own_citations": b.own_citations, "citation_rate": b.citation_rate,
        "citation_ci": list(b.citation_ci), "share_of_voice": b.share_of_voice,
        "owned_ratio": b.owned_ratio,
    } for b in brand_stats(db, **kw)]

    # Header counts must use the same engine filter as the figures below, or
    # the page claims more answers than its numbers are computed from.
    clause, params = _filters(**kw)
    conn = sqlite3.connect(db)
    n = conn.execute(
        f"SELECT COUNT(*) FROM responses r WHERE {clause}", params).fetchone()[0]
    ncit = conn.execute(
        f"SELECT COUNT(*) FROM citations c JOIN responses r "
        f"ON r.id = c.response_id WHERE {clause}", params).fetchone()[0]
    engines = [r[0] for r in conn.execute(
        f"SELECT DISTINCT r.engine FROM responses r WHERE {clause}", params)]
    lo, hi = (v or "" for v in conn.execute(
        f"SELECT MIN(r.run_ts), MAX(r.run_ts) FROM responses r WHERE {clause}",
        params).fetchone())
    trunc = conn.execute(
        f"SELECT COUNT(*) FROM responses r WHERE {clause} AND r.truncated=1",
        params).fetchone()[0]
    n_prompts = conn.execute(
        f"SELECT COUNT(DISTINCT r.prompt_id) FROM responses r WHERE {clause}",
        params).fetchone()[0]
    conn.close()

    label = ", ".join(ENGINE_NAMES.get(e, e) for e in sorted(engines))
    single = len(engines) == 1

    caveats = [
        f"{n} answers across {len({r['brand'] for r in rows})} brands and "
        f"{len(cfg.prompts)} questions, sampled repeatedly rather "
        f"than asked once, because the same question returns different answers "
        f"each time.",
        "Rates are reported with 95% Wilson confidence intervals. Where two "
        "intervals overlap, no ranking between those brands is claimed.",
        "Answers come from provider APIs with web search switched on, not "
        "from the chat interfaces people use. Commercial tools collect from "
        "the browser for that reason.",
        "\"Retrieved\" sources are what the search step returned, not what the "
        "answer used. They overstate how much any one source shaped an answer.",
        "The brand-named row rests on three prompts. The original monitor's "
        "482 of 482 is the stronger evidence for that effect, on one engine.",
        "Answer engines change, and so do the search results they read. This "
        "is a measurement of a moment, not a permanent property.",
    ]
    if single:
        caveats.insert(0, f"One engine only ({label}). These findings may not "
                          f"hold for other answer engines, and testing that is "
                          f"the next stage of this work.")
    if trunc:
        caveats.append(
            f"{trunc} of {n} answers ({trunc / max(n,1):.0%}) were cut off by a "
            f"response-length limit since corrected. Brands named late in an "
            f"answer are undercounted in this dataset.")

    return {
        "title": "Where a 61% AI visibility score came from",
        "standfirst": ("A brand monitor said one bootcamp appeared in 61% of AI "
                       "answers about its category. This measures how much of "
                       "that number was the monitor."),
        "n": n, "n_citations": ncit, "n_prompts": n_prompts or len(cfg.prompts),
        "engines": engines, "engines_label": label,
        "window": ((lo[:10] if lo[:10] == hi[:10] else f"{lo[:10]} to {hi[:10]}") if lo else ""),
        "brands": rows,
        "pooled_intent": pooled_by_intent(db, cfg, **kw),
        "monitor": MONITOR,
        "top_domains": top_domains(db, limit=15, **kw),
        "by_intent": {k: [{"brand": b.brand, "n": b.n,
                           "mention_rate": b.mention_rate}
                          for b in v]
                      for k, v in by_intent(db, **kw).items() if v},
        "significance": significance_matrix(brand_stats(db, **kw)),
        "cross_model": cross_model_agreement(db, **kw),
        "separate_engines": [
            e for x in (exclude_engines or [])
            for e in engine_summary(db, engine=x)],
        "headline_claim": HEADLINE_CLAIM, "intent_para": INTENT_PARA,
        "formula_para": FORMULA_PARA, "monitor_para": MONITOR_PARA,
        "monitor_context": MONITOR_CONTEXT, "citation_note": CITATION_NOTE,
        "discarded": DISCARDED, "caveats": caveats,
        "footer": ("Method, code and data are open. Flatiron School is one of "
                   "the ten brands; the author built the original monitor "
                   "there and left in September 2026. The monitor's own data "
                   "is not published."),
        "generated": datetime.now(timezone.utc).strftime("%d %B %Y"),
    }


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="study")
    p.add_argument("--config", default="config.study.yaml")
    p.add_argument("--db", default=None)
    p.add_argument("--out", default="public/study/index.html")
    p.add_argument("--json", default="public/study/results.json")
    p.add_argument("--exclude-truncated", action="store_true")
    p.add_argument("--exclude-engine", action="append", default=[],
                   help="drop an engine from the figures but keep its rows "
                        "(repeatable). Use for an ungrounded engine whose "
                        "numbers you report separately.")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    db = args.db or cfg.database
    if not Path(db).exists():
        sys.exit(f"No database at {db}. Run the pipeline first, or pass --db.")

    d = collect(db, cfg, exclude_truncated=args.exclude_truncated,
                exclude_engines=args.exclude_engine)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(d, indent=2), encoding="utf-8")
    Path(args.out).write_text(build_html(d), encoding="utf-8")

    print(f"Wrote {args.out}")
    print(f"Wrote {args.json}")
    print(f"  {d['n']} answers, {d['n_citations']} citations, "
          f"{len(d['brands'])} brands, engines: {d['engines_label']}")
    print("\nPreview locally:  python3 -m http.server -d public 8000")
    print("Then open:        http://localhost:8000/study/")


if __name__ == "__main__":
    main()
