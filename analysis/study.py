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
# These strings are typed by the author. Where they quote a figure, it comes
# from the run with Gemini excluded (270 answers across Claude, ChatGPT and
# Perplexity) or from the original monitor (see MONITOR). If the data or the
# engine set changes, re-check every number here by hand. Nothing updates them.
# =============================================================================
TITLE = "61% visible. 39 points of it were the prompt list."
TITLE_HTML = "61% visible.<br>39 points of it were the prompt list."

STANDFIRST = (
    "I built an AI visibility monitor for a coding bootcamp. It reported the "
    "brand in 61% of AI answers about its category. Then I measured how much "
    "of that number was the monitor, re-ran the question across ten brands and "
    "three engines, and threw out five findings on the way. This is what held.")

AUTHOR = {
    "name": "Tonishqa Kaplish",
    "role": "Marketing technologist, Seattle. I build marketing data systems, "
            "then check whether they're telling the truth.",
    "links": [
        ("LinkedIn", "https://www.linkedin.com/in/tonishqa"),
        ("GitHub", "https://github.com/tkaplish888-alt/ai-visibility-audit"),
        ("Portfolio", "https://tonishqakaplish.com"),
        ("Email", "mailto:tkaplish888@gmail.com"),
        ("Book 30 minutes", "https://calendly.com/tkaplish888/30min"),
    ],
}

# The original monitor's database was collected for a former employer and is
# not in this repository. These figures are typed from it by hand: the last of
# its six weekly runs (31 Aug 2026, Claude only, 30 prompts, 147 answers), and
# all six runs for the brand-named total.
MONITOR = {
    "brand": "Flatiron School",
    "named_prompts": 12, "total_prompts": 30,
    "named_answers": 57, "named_hits": 57,
    "other_answers": 90, "other_hits": 33,
    "total_answers": 147, "total_hits": 90,
    "all_runs_named": 482,
}

PULL = (
    "Put a brand's name in the prompt and the answer names it back. Every "
    "time. Ask an open question and most brands disappear.")

INTENT_PARA = (
    "The mechanism is plain. Commercial questions get a shortlist: 95% of them "
    "name at least one of the ten schools. Informational questions get an "
    "essay: 85% name nobody. No engine answers \"are bootcamps worth it?\" with "
    "a recommendation, so on that prompt no brand is visible, and no content "
    "strategy changes that.")

FORMULA_PARA = (
    "Which makes a visibility score arithmetic. Take the share of prompts that "
    "name the brand and multiply by 100%. Take the share that don't and "
    "multiply by the brand's real rate on open questions. Add them. The first "
    "term belongs to whoever wrote the prompt list. Only the second belongs to "
    "the brand.")

MONITOR_PARA = (
    "This study exists because of that dashboard. I built it as a weekly "
    "monitor for one bootcamp's marketing team, and it said the brand appeared "
    "in 61% of AI answers about its category. Twelve of its thirty prompts "
    "named the brand. Across six weekly runs, all 482 answers to those prompts "
    "named it back, because the question contained the name. On the other "
    "eighteen prompts, same engine, same week: 37%.")

MONITOR_CONTEXT = (
    "The monitor's 37% and this study's {other} are different measurements: "
    "one engine in August on prompts written to watch one brand, against "
    "three engines in October on prompts written to survey a category. The "
    "gap between them is the ordinary noise of this kind of work. The gap "
    "between either of them and 61 is the panel.")

BRAND_PARA = (
    "Springboard is separable from Hack Reactor and every brand below it. "
    "Nothing else is separable from its neighbours. A ranked bar chart of "
    "this table would be asserting an order the data can't support. My own "
    "dashboard drew it that way.")

CODECADEMY_NOTE = (
    "Codecademy's zero is real, checked against the raw payloads: Perplexity "
    "retrieved Codecademy pages for three answers and used none of them. The "
    "matcher catches every spelling variant. I checked that too.")

CITATION_NOTE = (
    "What Perplexity and Claude return as \"citations\" is what their search "
    "step retrieved, not what the answer used. One answer here retrieves a "
    "Nucamp page and then talks entirely about a different school. So a "
    "citation rate is a retrieval rate. Read this table as what the engines "
    "looked at, not what persuaded them.")

DISCARDED_INTRO = (
    "A study that only shows what survived is hiding the method. Each of "
    "these looked publishable for about a day.")

DISCARDED = [
    ("Content farms out-cite the brands' own sites.",
     "A manual check of five flagged domains found a program-matching search "
     "tool, a paid tutoring service and a legitimate developer academy. The "
     "classification was a judgement, and it was wrong often enough that the "
     "number meant nothing."),
    ("Reddit barely registers.",
     "Community sources were under 1% of retrieved sources. Reddit's citation "
     "share in ChatGPT had already collapsed in August 2026, and that was "
     "widely covered. I had rediscovered a known event."),
    ("API results differ from what users see, so commercial tools measure "
     "the wrong thing.",
     "Backwards. Commercial tools collect from the browser to avoid exactly "
     "that discrepancy. This study runs on APIs. The critique described my "
     "own limitation."),
    ("Perplexity grounds every answer; Gemini grounds almost none.",
     "Perplexity was given an explicit search instruction during the run and "
     "Gemini was not. I had confounded the comparison with my own "
     "configuration."),
    ("Some brands are cited far more often than they are named.",
     "Nucamp's site appeared in the returned sources of 101 answers while its "
     "name appeared in about half as many. That is a fact about what "
     "\"citation\" means for these engines (retrieved, not used), not about "
     "Nucamp. It changed the README more than anything else did."),
]

CHECKS_INTRO = (
    "Five things I'd check in any visibility tool before trusting its number, "
    "including the one I built.")

CHECKS = [
    ("Count the prompts that name you.",
     "Divide by the total. That fraction is a floor under your score that has "
     "nothing to do with the engine."),
    ("Report branded and unbranded prompts separately.",
     "Branded prompts measure accuracy and perception. They don't measure "
     "visibility."),
    ("Tag every prompt with an intent and cut every rate by it.",
     "Informational prompts read near zero for everyone. That isn't a gap to "
     "close with content. It's how the engines answer those questions."),
    ("Show counts and intervals, not bare percentages.",
     "\"20% (55 of 270, 16% to 26%)\" tells a reader what they can conclude. "
     "\"20%\" invites them to rank."),
    ("Ask what \"citation\" means in your tool.",
     "Retrieved, or used by the answer? They are different numbers. Find out "
     "which one your dashboard is showing."),
]

FACTS = [
    ("Prompts", "30, three of them naming a brand"),
    ("Brands", "10, as peers"),
    ("Engines", "ChatGPT, Claude, Perplexity"),
    ("Samples", "3 per prompt per engine"),
    ("Collected", "1 October 2026, provider APIs"),
    ("Cost", "about $20"),
]
# =============================================================================

# --- palette ---------------------------------------------------------------
# Cool pale ground and deep slate-teal ink. Teal carries measured data; plum
# marks what the prompt list contributed and what was thrown out.
C = {
    "ground": "#F3F5F4",
    "panel": "#FFFFFF",
    "ink": "#15282C",
    "teal": "#1F6F6B",
    "teal_soft": "#9CC4C0",
    "plum": "#7A2E5B",
    "grey": "#66736F",
    "rule": "#D5DCDA",
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


def brand_split(db: str, cfg, brand: str, **kw) -> dict:
    """One brand's mention rate on prompts that name it versus prompts that
    don't. The study-side counterpart of the MONITOR table."""
    import re
    ent = next(e for e in cfg.all_entities if e.name == brand)
    names = {pr.id for pr in cfg.prompts
             if any(re.search(rf"\b{re.escape(a)}\b", pr.text, re.I)
                    for a in (ent.aliases or [ent.name]))}
    clause, params = _filters(**kw)
    conn = sqlite3.connect(db)
    q = (f"SELECT r.prompt_id, (m.count > 0) FROM mentions m JOIN responses r "
         f"ON r.id = m.response_id WHERE m.entity = ? AND {clause}")
    out = {"named": [0, 0], "other": [0, 0]}
    for pid, hit in conn.execute(q, [brand, *params]):
        t = out["named" if pid in names else "other"]
        t[0] += int(hit); t[1] += 1
    conn.close()
    out["named_prompts"] = len(names)
    out["other_prompts"] = len(cfg.prompts) - len(names)
    return out


# --- page ------------------------------------------------------------------

def build_html(d: dict) -> str:
    rows = d["brands"]
    disputed = [s for s in d["significance"] if not s["distinguishable"]][:6]
    pooled = d["pooled_intent"]
    m = d["monitor"]
    sp = d["study_split"]

    # --- hero split bar: the 61 points, decomposed ------------------------
    named_pts = m["named_hits"] / m["total_answers"] * 100
    other_pts = m["other_hits"] / m["total_answers"] * 100
    total_pts = m["total_hits"] / m["total_answers"] * 100

    # --- tables -----------------------------------------------------------
    def _rate(r):
        return "100%" if r["k"] == r["n"] else f"{r['rate']:.1%}"
    pooled_rows = "".join(
        f'<tr><td>{esc(r["label"])}<br><span class="ex">"{esc(r["example"])}"'
        f'</span></td><td class="n">{_rate(r)}</td>'
        f'<td class="n">{r["k"]:,} of {r["n"]:,}</td>'
        f'<td class="t">{r["ci"][0]:.1%} to {r["ci"][1]:.1%}</td></tr>'
        for r in pooled)

    def row(label, prompts, n, k, strong=False):
        tag = "b" if strong else "span"
        return (f'<tr><td><{tag}>{esc(label)}</{tag}></td><td class="n">{prompts}</td>'
                f'<td class="n">{n}</td><td class="n"><{tag}>{k} '
                f'({k / n:.0%})</{tag}></td></tr>')
    split_rows = (
        f'<tr class="grp"><th colspan="4">The monitor, final weekly run '
        f'(Claude, August 2026)</th></tr>'
        + row("Prompts naming the brand", m["named_prompts"], m["named_answers"], m["named_hits"])
        + row("Prompts that don't", m["total_prompts"] - m["named_prompts"], m["other_answers"], m["other_hits"])
        + row("As reported", m["total_prompts"], m["total_answers"], m["total_hits"], strong=True)
        + f'<tr class="grp"><th colspan="4">This study, same brand '
          f'({esc(d["engines_label"])}, October 2026)</th></tr>'
        + row("Prompts naming the brand", sp["named_prompts"], sp["named"][1], sp["named"][0])
        + row("Prompts that don't", sp["other_prompts"], sp["other"][1], sp["other"][0])
        + row("All prompts", sp["named_prompts"] + sp["other_prompts"],
              sp["named"][1] + sp["other"][1], sp["named"][0] + sp["other"][0], strong=True))
    other_rate = sp["other"][0] / max(sp["other"][1], 1)
    monitor_context = d["monitor_context"].format(other=f"{other_rate:.0%}")

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

    overlaps = ", ".join(
        f'{esc(s["a"])} and {esc(s["b"])}' for s in disputed)

    discarded = "".join(f'<li><b>{esc(t)}</b><span>{esc(w)}</span></li>'
                        for t, w in d["discarded"])
    checks = "".join(f'<li><b>{esc(t)}</b><span>{esc(w)}</span></li>'
                     for t, w in d["checks"])

    separate = ""
    for e in d.get("separate_engines", []):
        name = ENGINE_NAMES.get(e["engine"], e["engine"])
        separate += (
            f'<section id="gemini"><h2>{esc(name)}, reported separately</h2>'
            f'<p>{esc(name)} is left out of every figure above. It returned no '
            f'grounding data at all for {e["ungrounded"]} of its {e["answers"]} '
            f'answers, which means it answered from training data rather than '
            f'the live web, and its remaining sources arrive through a redirect '
            f'host the parser does not yet resolve. Its answers stay in the '
            f'database.</p></section>')

    links = "".join(
        f'<li><a href="{esc(u)}">{esc(t)}</a></li>'
        for t, u in d["author"]["links"])
    facts = "".join(f'<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>'
                    for k, v in d["facts"])
    toc = [("ask", "Ask differently, get a different brand"),
           ("sixty-one", "The 61%, taken apart"),
           ("brands", "Ten brands, one cluster"),
           ("retrieved", "Retrieved, not used"),
           ("discarded", "Five findings I threw out"),
           ("checks", "Five checks before you trust a score"),
           ("limits", "What this can't tell you")]
    toc_html = "".join(f'<li><a href="#{i}">{esc(t)}</a></li>' for i, t in toc)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(d["title"])}</title>
<meta name="description" content="How prompt wording sets AI visibility scores: {d["n"]} answers about coding bootcamps across three answer engines, with the arithmetic and the findings that were thrown out.">
<meta property="og:title" content="{esc(d["title"])}">
<meta property="og:description" content="{esc(d["standfirst"])}">
<meta property="og:image" content="prompt-effect.png">
<meta name="author" content="{esc(d["author"]["name"])}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400;1,500&display=swap" rel="stylesheet">
<style>
  :root {{
    --ground:{C["ground"]}; --panel:{C["panel"]}; --ink:{C["ink"]};
    --teal:{C["teal"]}; --teal-soft:{C["teal_soft"]}; --plum:{C["plum"]};
    --grey:{C["grey"]}; --rule:{C["rule"]};
  }}
  * {{ box-sizing:border-box; }}
  html {{ scroll-behavior:smooth; }}
  @media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior:auto; }} }}
  body {{
    margin:0; background:var(--ground); color:var(--ink);
    font:400 17px/1.6 "Schibsted Grotesk", ui-sans-serif, system-ui, sans-serif;
    -webkit-font-smoothing:antialiased;
  }}
  a {{ color:var(--teal); text-decoration-thickness:1px; text-underline-offset:3px; }}
  a:hover {{ color:var(--ink); }}
  :focus-visible {{ outline:2px solid var(--teal); outline-offset:3px; }}

  .shell {{ max-width:1080px; margin:0 auto; padding:0 28px 96px; }}

  /* hero */
  .hero {{ padding:72px 0 44px; border-bottom:1px solid var(--rule); }}
  h1 {{
    font-size:clamp(36px,5.2vw,62px); line-height:1.0; font-weight:800;
    letter-spacing:-0.035em; margin:0 0 22px; max-width:17ch; text-wrap:balance;
  }}
  .standfirst {{ font-size:20px; line-height:1.5; margin:0; max-width:58ch; }}
  .split {{ margin:40px 0 0; max-width:720px; }}
  .split .track {{
    display:flex; height:34px; background:var(--panel);
    border:1px solid var(--rule); border-radius:4px; overflow:hidden;
  }}
  .split .seg {{ height:100%; display:block; }}
  .split .seg.plum {{ background:var(--plum); }}
  .split .seg.teal {{ background:var(--teal); }}
  .split .scale {{ display:flex; justify-content:space-between; font-size:12px;
                   color:var(--grey); margin-top:6px; }}
  .split .keys {{ display:flex; flex-wrap:wrap; gap:8px 28px; margin:14px 0 0;
                  padding:0; list-style:none; font-size:15px; }}
  .split .keys li {{ display:flex; align-items:baseline; gap:9px; }}
  .split .keys i {{ width:12px; height:12px; border-radius:2px; display:inline-block;
                    transform:translateY(1px); }}
  .split .keys b {{ font-weight:700; font-variant-numeric:tabular-nums; }}
  .split figcaption {{ font-size:14px; color:var(--grey); margin-top:10px; }}

  /* two-column body with a sticky rail */
  .layout {{
    display:grid; grid-template-columns:minmax(0,680px) 264px;
    column-gap:72px; justify-content:space-between;
  }}
  main {{ min-width:0; }}
  aside {{ position:sticky; top:24px; align-self:start; margin-top:72px; }}
  .links.mobile-links {{ display:none; margin-top:28px; }}
  .card {{
    background:var(--panel); border:1px solid var(--rule); border-radius:8px;
    padding:20px 22px; margin-bottom:18px;
  }}
  .card h3 {{ margin:0 0 6px; font-size:17px; font-weight:700; }}
  .card .role {{ margin:0 0 14px; font-size:14px; line-height:1.45; color:var(--grey); }}
  .links {{ list-style:none; padding:0; margin:0; display:flex; flex-wrap:wrap; gap:8px; }}
  .links a {{
    display:inline-block; padding:6px 11px; border:1px solid var(--rule);
    border-radius:999px; font-size:14px; font-weight:500; text-decoration:none;
    color:var(--ink); background:var(--ground);
  }}
  .links a:hover {{ border-color:var(--teal); color:var(--teal); }}
  .toc h4, .facts h4 {{ margin:0 0 8px; font-size:13px; font-weight:600;
                        color:var(--grey); }}
  .toc ol {{ list-style:none; margin:0; padding:0; }}
  .toc li {{ padding:5px 0; border-top:1px solid var(--rule); font-size:14.5px; line-height:1.35; }}
  .toc li:first-child {{ border-top:0; }}
  .toc a {{ color:var(--ink); text-decoration:none; }}
  .toc a:hover {{ color:var(--teal); }}
  .facts dl {{ margin:0; }}
  .facts div {{ display:grid; grid-template-columns:76px 1fr; gap:10px;
                padding:5px 0; border-top:1px solid var(--rule); font-size:14px; }}
  .facts div:first-child {{ border-top:0; }}
  .facts dt {{ color:var(--grey); margin:0; }}
  .facts dd {{ margin:0; }}

  /* content */
  section {{ padding-top:56px; }}
  h2 {{
    font-size:30px; line-height:1.12; font-weight:700; letter-spacing:-0.02em;
    margin:0 0 8px;
  }}
  h3 {{ font-size:19px; font-weight:700; margin:34px 0 6px; }}
  .sub {{ color:var(--grey); font-size:16px; margin:0 0 20px; }}
  p {{ margin:0 0 18px; }}
  figure {{ margin:22px 0 10px; }}
  svg {{ width:100%; height:auto; display:block; }}
  figcaption {{ font-size:14px; color:var(--grey); margin-top:10px; }}
  img {{ max-width:100%; height:auto; display:block; border:1px solid var(--rule);
         border-radius:6px; }}
  .pull {{
    border-left:3px solid var(--plum); padding:2px 0 2px 20px; margin:30px 0;
    font-size:23px; line-height:1.38; font-weight:500; letter-spacing:-0.01em;
  }}
  table {{ width:100%; border-collapse:collapse; margin:18px 0 22px; font-size:15.5px; }}
  th, td {{ text-align:left; padding:9px 12px 9px 0; border-bottom:1px solid var(--rule);
            vertical-align:top; }}
  th {{ font-weight:600; font-size:13.5px; color:var(--grey); }}
  tr.grp th {{ padding-top:18px; color:var(--ink); font-size:14px; }}
  td.n, th.n {{ text-align:right; font-variant-numeric:tabular-nums;
                white-space:nowrap; padding-left:16px; }}
  td.t {{ color:var(--grey); }}
  .ex {{ color:var(--grey); font-size:13.5px; font-style:italic; }}
  ol.list {{ padding:0; margin:18px 0 0; list-style:none; counter-reset:item; }}
  ol.list li {{
    display:grid; grid-template-columns:34px 1fr; gap:0 8px; padding:16px 0;
    border-top:1px solid var(--rule); counter-increment:item;
  }}
  ol.list li::before {{
    content:counter(item); font-weight:700; font-size:20px; line-height:1.2;
    color:var(--plum); grid-row:1 / span 2;
  }}
  ol.list li b {{ font-weight:700; display:block; margin-bottom:3px; }}
  ol.list li span {{ display:block; color:var(--ink); }}
  ol.list.checks li::before {{ color:var(--teal); }}
  .limits {{
    background:var(--panel); border:1px solid var(--rule); border-radius:8px;
    padding:24px 28px; margin-top:56px;
  }}
  .limits h2 {{ font-size:22px; margin-bottom:12px; }}
  .limits ul {{ margin:0; padding-left:20px; }}
  .limits li {{ margin-bottom:9px; font-size:15.5px; }}
  footer {{
    margin-top:56px; padding-top:18px; border-top:1px solid var(--rule);
    font-size:14.5px; color:var(--grey);
  }}

  @media (max-width:940px) {{
    .layout {{ grid-template-columns:1fr; }}
    aside {{ position:static; margin-top:40px; }}
    .links.mobile-links {{ display:flex; }}
  }}
  @media (max-width:640px) {{
    body {{ font-size:16px; }}
    .shell {{ padding:0 18px 72px; }}
    .hero {{ padding-top:40px; }}
    .standfirst {{ font-size:18px; }}
    h2 {{ font-size:26px; }}
    .pull {{ font-size:20px; }}
    section {{ padding-top:44px; }}
  }}
</style>
</head>
<body>
<div class="shell">
<div class="layout">
<main>

<header class="hero">
  <h1>{d["title_html"]}</h1>
  <p class="standfirst">{esc(d["standfirst"])}</p>
  <figure class="split" aria-label="The reported 61% split into its two sources">
    <div class="track">
      <span class="seg plum" style="width:{named_pts:.1f}%"></span>
      <span class="seg teal" style="width:{other_pts:.1f}%"></span>
    </div>
    <div class="scale"><span>0%</span><span>{total_pts:.0f}% reported</span><span>100%</span></div>
    <ul class="keys">
      <li><i style="background:var(--plum)"></i><span><b>{named_pts:.0f} points</b> from prompts that named the brand</span></li>
      <li><i style="background:var(--teal)"></i><span><b>{other_pts:.0f} points</b> from the brand on every other prompt</span></li>
    </ul>
    <figcaption>The monitor's final weekly run: {m["total_answers"]} answers,
    {m["total_prompts"]} prompts, {m["named_prompts"]} of them naming the brand.</figcaption>
  </figure>
  <ul class="links mobile-links">{links}</ul>
</header>

<section id="ask">
<h2>Ask differently, get a different brand</h2>
<p class="sub">Share of answers naming a given brand, by prompt type. Every
brand checked against every answer, pooled across all ten.</p>
<figure>
  {chart_intent(pooled)}
  <figcaption>Whiskers are 95% Wilson confidence intervals. No two rows
  overlap.</figcaption>
</figure>
<p class="pull">{esc(d["pull"])}</p>
<table>
  <thead><tr><th>Prompt type</th><th class="n">Rate</th>
  <th class="n">Brand-answer pairs</th><th class="t">95% interval</th></tr></thead>
  <tbody>{pooled_rows}</tbody>
</table>
<p>{esc(d["intent_para"])}</p>
<p>{esc(d["formula_para"])}</p>
</section>

<section id="sixty-one">
<h2>The 61%, taken apart</h2>
<figure>
  <img src="monitor-before.png" alt="The original monitor's dashboard, reporting a 61% mention rate for one brand across 147 Claude answers." loading="lazy">
  <figcaption>The dashboard that started this, as published in August 2026.</figcaption>
</figure>
<p>{esc(d["monitor_para"])}</p>
<table>
  <thead><tr><th></th><th class="n">Prompts</th>
  <th class="n">Answers</th><th class="n">Brand named</th></tr></thead>
  <tbody>{split_rows}</tbody>
</table>
<p>{esc(monitor_context)}</p>
</section>

<section id="brands">
<h2>Ten brands, one cluster</h2>
<p class="sub">Mention rate across every prompt, with its 95% confidence interval.</p>
<figure>
  {chart_intervals(rows)}
  <figcaption>The bar is the range the true rate is very likely to fall in.
  The tick is what was measured. Where two bars overlap, the difference between
  those brands is not real at this sample size, however different the
  percentages look.</figcaption>
</figure>
<p>{esc(d["brand_para"])}{(" Pairs you cannot separate: " + overlaps + ".") if overlaps else ""}</p>
<p>{esc(d["codecademy_note"])}</p>
<h3>By prompt type, per brand</h3>
<table>
  <thead><tr><th>Prompt type</th><th class="n">Answers</th>
  <th class="t">Most named</th></tr></thead>
  <tbody>{intent_rows}</tbody>
</table>
</section>

<section id="retrieved">
<h2>Retrieved, not used</h2>
<p class="sub">The fifteen websites the engines' search step returned most often.</p>
<p>{esc(d["citation_note"])}</p>
<table>
  <thead><tr><th>Domain</th><th class="n">Retrieved</th>
  <th class="t">Type</th></tr></thead>
  <tbody>{domain_rows}</tbody>
</table>
</section>

<section id="discarded">
<h2>Five findings I threw out</h2>
<p class="sub">{esc(d["discarded_intro"])}</p>
<ol class="list">{discarded}</ol>
</section>

<section id="checks">
<h2>Five checks before you trust a visibility score</h2>
<p class="sub">{esc(d["checks_intro"])}</p>
<ol class="list checks">{checks}</ol>
</section>

{separate}

<div class="limits" id="limits">
  <h2>What this can't tell you</h2>
  <ul>{"".join(f"<li>{esc(c)}</li>" for c in d["caveats"])}</ul>
</div>

<footer>
  <p>{esc(d["footer"])}<br>
  <a href="https://github.com/tkaplish888-alt/ai-visibility-audit">Code, data and both prompt panels on GitHub</a>.
  Generated {esc(d["generated"])}.</p>
</footer>

</main>

<aside>
  <div class="card">
    <h3>{esc(d["author"]["name"])}</h3>
    <p class="role">{esc(d["author"]["role"])}</p>
    <ul class="links">{links}</ul>
  </div>
  <div class="card toc">
    <h4>On this page</h4>
    <ol>{toc_html}</ol>
  </div>
  <div class="card facts">
    <h4>This study</h4>
    <dl>{facts}</dl>
  </div>
</aside>
</div>
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
        f"{n} answers, {len({r['brand'] for r in rows})} brands, "
        f"{len(cfg.prompts)} prompts, one category, one day. Engines and "
        f"their indexes move; this is a measurement of a moment.",
        "Answers come from provider APIs with web search switched on, not "
        "from the chat interfaces people use. Commercial tools collect from "
        "the browser for that reason.",
        "Every rate carries a 95% Wilson interval. Where two overlap, no "
        "ranking is claimed.",
        "The brand-named row rests on three prompts and 54 pairs. The "
        "original monitor's 482 of 482 is the stronger evidence for that "
        "effect, on one engine.",
        "\"Retrieved\" sources are what the search step returned, not what "
        "the answer used. They overstate how much any one source shaped an "
        "answer.",
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
        "title": TITLE, "title_html": TITLE_HTML, "standfirst": STANDFIRST,
        "author": AUTHOR,
        "facts": FACTS,
        "n": n, "n_citations": ncit, "n_prompts": n_prompts or len(cfg.prompts),
        "engines": engines, "engines_label": label,
        "window": ((lo[:10] if lo[:10] == hi[:10] else f"{lo[:10]} to {hi[:10]}") if lo else ""),
        "brands": rows,
        "pooled_intent": pooled_by_intent(db, cfg, **kw),
        "monitor": MONITOR,
        "study_split": brand_split(db, cfg, MONITOR["brand"], **kw),
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
        "pull": PULL, "intent_para": INTENT_PARA,
        "formula_para": FORMULA_PARA, "monitor_para": MONITOR_PARA,
        "monitor_context": MONITOR_CONTEXT, "brand_para": BRAND_PARA,
        "codecademy_note": CODECADEMY_NOTE, "citation_note": CITATION_NOTE,
        "discarded_intro": DISCARDED_INTRO, "discarded": DISCARDED,
        "checks_intro": CHECKS_INTRO, "checks": CHECKS, "caveats": caveats,
        "footer": ("Flatiron School is one of the ten brands. I built the "
                   "original monitor as Marketing Technology Lead there and "
                   "left in September 2026. The monitor's data was collected "
                   "for them and is not published; the study's data, collected "
                   "afterwards with my own keys, is."),
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
