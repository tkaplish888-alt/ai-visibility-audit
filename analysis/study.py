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
# Where these strings quote a figure, it comes from the run with Gemini
# excluded (270 answers across Claude, ChatGPT and Perplexity) or from the
# original monitor (see MONITOR). If the data or the engine set changes,
# re-check every number here by hand. Nothing updates them.
# =============================================================================
TITLE = ("Inside the AI Visibility Measurement Problem: What Inflates the "
         "Score, What a Citation Really Measures, and 5 Findings That Didn't "
         "Survive")
TITLE_MAIN = "Inside the AI Visibility Measurement Problem:"
TITLE_SUB = ("What inflates the score, what a citation really measures, "
             "and 5 findings that didn't survive")

STANDFIRST = (
    "I set out to measure how often AI engines mention a brand. My first "
    "tracker said 61%, and most of that number came from the prompts I'd "
    "chosen. This is what I learned rebuilding it across ten brands and three "
    "engines, and the method I'd use now.")

HOOK = (
    "There's no agreed way to measure AI visibility yet. Prompts have no "
    "search volume the way keywords do, the same prompt can return a "
    "different answer every time you run it, and every score rests on choices "
    "made before the first query: which prompts to track, how often to sample "
    "them, how to roll the answers into one number. Those choices are easy to "
    "make without noticing. This study makes them on purpose, and shows how "
    "much each one moves the result.")

AUTHOR = {
    "name": "Tonishqa Kaplish",
    "role": "Marketing technologist in Seattle. Builds the attribution, AI "
            "tooling and measurement systems behind go-to-market teams.",
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
# all six runs for the branded total.
MONITOR = {
    "brand": "Flatiron School",
    "named_prompts": 12, "total_prompts": 30,
    "named_answers": 57, "named_hits": 57,
    "other_answers": 90, "other_hits": 33,
    "total_answers": 147, "total_hits": 90,
    "all_runs_named": 482,
}

SHORT_INTRO = (
    "When a prompt names a brand, AI engines name it back. In this study that "
    "happened every time. Take the brand's name out, and how often it shows "
    "up depends on the prompt's intent: the kind of question being asked.")

SHORT_OUTRO = (
    "So \"what share of AI answers mention us?\" has no single answer. It has "
    "one per prompt intent, and the mix of prompts in your panel decides "
    "which one you get.")

WHY_1 = (
    "The prompt panel is the fixed set of prompts a tracker asks, and it is "
    "the first of those choices. Prompt selection is already part of the "
    "conversation in this field. What I wanted to add was a worked example: "
    "one real tracker taken apart, a test of whether the pattern holds across "
    "a whole category, and a method that comes out the other side.")

WHY_2 = (
    "Everything below comes from two datasets. The first is the tracker I "
    "built and ran weekly for six weeks. The second is a neutral panel I "
    "designed afterwards and ran across ten brands and three engines. Both "
    "prompt lists are in the repository, side by side.")

SIXTY_ONE_1 = (
    "My first tracker ran a 30-prompt panel through Claude every week, "
    "sampling each prompt five times, and reported how often one bootcamp was "
    "named. It settled at 61%. That looked like a strong result until I split "
    "the panel by whether each prompt contained the brand's name.")

SIXTY_ONE_2 = (
    "Across all six weekly runs, the tracker's branded prompts returned 482 "
    "answers, and every one of them named the brand. That isn't the engine "
    "choosing to recommend anyone. The brand was in the prompt.")

ARITH_1 = "It's one weighted average. Three terms to know first:"

ARITH_TERMS = [
    ("Rate", "the share of answers that named the brand (answers naming it, "
             "divided by all answers)"),
    ("Branded rate", "that share for prompts with the brand's name in them"),
    ("Unbranded rate", "that share for prompts without it"),
]

ARITH_2 = (
    "The dashboard's single number blends the two groups, and each group "
    "counts in proportion to how many answers it produced:")

ARITH_3 = "Plugging in the tracker's final run:"

ARITH_READ = [
    "Branded prompts produced 57 of the 147 answers (38.8%), and all 57 named "
    "the brand, so they add 38.8 points.",
    "Unbranded prompts produced the other 90 answers (61.2%) and named the "
    "brand 36.7% of the time, so they add 61.2% × 36.7% = 22.4 points.",
    "Together that's 61.2, the number on the dashboard.",
]

ARITH_5 = (
    "Because branded prompts name the brand every time, their share of answers "
    "goes straight into the score. If half your answers come from branded "
    "prompts, half your score is set before any engine answers. Here, 39 of "
    "the 61 points came from the prompt list. The other 22 reflect the brand.")

CATEGORY_INTRO = (
    "One tracker could be a fluke, so I built a neutral panel and ran it "
    "across the whole category.")

STUDY_DESIGN = [
    ("Brands", "10 coding bootcamps, all treated as peers"),
    ("Prompt panel", "30 prompts: 12 commercial, 12 informational, "
                     "3 transactional, 3 branded"),
    ("Branded prompts", "Only the 3 head-to-head comparisons, kept in on "
                        "purpose to measure the branded effect in the same run"),
    ("Engines", "ChatGPT (gpt-5), Claude (claude-sonnet-5), Perplexity "
                "(sonar), each through its API with web search on"),
    ("Sampling", "3 runs per prompt per engine, because answers vary from "
                 "run to run"),
    ("Collection", "1 October 2026: 360 answers, zero failures, about $20"),
    ("Uncertainty", "95% Wilson intervals on every rate. This method stays "
                    "accurate near 0% and 100%, where the textbook formula "
                    "breaks down."),
]

CATEGORY_HELD = (
    "The pattern held for every brand: 100% on branded prompts, then a steady "
    "decline through commercial, transactional and informational.")

CATEGORY_MECH_INTRO = (
    "The mechanism shows up when you count answers that named any of the ten "
    "brands:")

CATEGORY_MECH = (
    "Commercial prompts get a shortlist. Informational prompts mostly get an "
    "explainer that recommends nobody. A low mention rate on informational "
    "prompts isn't necessarily a gap to close: most of those answers don't "
    "name any brand.")

SAME_BRAND_INTRO = (
    "Here is the same brand from the original tracker, measured both ways:")

SAME_BRAND_OUTRO = (
    "The 37% and the {other} aren't the same measurement. One is a single "
    "engine in August, on a panel written to monitor one brand. The other is "
    "three engines in October, on a panel written to survey the category. "
    "Both sit far below 61%. The distance down from 61% is the part the panel "
    "design was responsible for.")

INTENT_GUIDE_INTRO = (
    "Every intent is useful, as long as you know what it's measuring.")

INTENT_GUIDE = [
    ("Branded", "Is [brand] worth it?",
     "What engines say about you when someone already knows your name, and "
     "whether they get the facts right",
     "Accuracy and reputation checks. Keep it out of your visibility score."),
    ("Commercial", "Best bootcamp for career changers?",
     "Whether you make the shortlist at the moment of choice",
     "Your headline visibility metric"),
    ("Transactional", "How do I pay for a bootcamp?",
     "Whether you come up as an example while someone plans a next step",
     "A secondary visibility signal"),
    ("Informational", "Are bootcamps still worth it?",
     "How engines frame the category, which rarely involves naming brands",
     "Topic coverage, not brand tracking"),
]

CITATION_1 = (
    "When an engine searches the web before answering, its API can return a "
    "list of sources. It's tempting to read that list as the sources the "
    "answer used. For Perplexity and Claude, it's actually what the search "
    "step retrieved, which isn't the same as what the answer used.")

CITATION_2 = (
    "One answer made this obvious. The engine retrieved a bootcamp's own "
    "page, then wrote an answer that never mentioned that bootcamp and "
    "focused entirely on a different school. It wasn't an isolated case:")

CITATION_GAP = [
    ("Answers where one brand's site appeared in the retrieved sources", 101),
    ("Answers that actually named that brand", 50),
]

CITATION_3 = (
    "That makes a citation rate a retrieval rate. It tells you which pages "
    "the engine looked at, not which ones shaped the answer.")

RETRIEVAL_CAN = [
    ("Your pages are findable by the engine's search step",
     "Your pages influenced what the answer said"),
    ("Which domains engines tend to pull up in your category",
     "Which domains engines trust or agree with"),
    ("Whether you're in the pool of candidate sources",
     "Whether you were recommended"),
]

CITATION_4 = (
    "Some APIs do mark usage. Claude's, for example, attaches citations to "
    "the specific sentences that draw on them, which makes it possible to "
    "separate retrieved sources from used ones. That's the natural next "
    "version of this work. In the meantime, if your tool reports citations, "
    "the most useful question to ask is which of the two it's counting.")

CLUSTER_PARA = (
    "Across 270 answers, most of the field sits inside overlapping intervals. "
    "The honest reading is a large group of brands at roughly the same level, "
    "with a few separated above and below it. A ranked bar chart of the same "
    "numbers would imply an order the sample can't support. My first "
    "dashboard drew exactly that chart, which is part of why I rebuilt it.")

ENGINE_INTRO = "The same brand, on unbranded prompts only:"

ENGINE_JACCARD_INTRO = (
    "The engines broadly agree on which brands belong in the category. For "
    "each prompt, I compared the set of brands each engine named using the "
    "Jaccard index: the brands both engines named, divided by all brands "
    "either one named. A score of 1.0 means identical sets.")

ENGINE_OUTRO = (
    "The engines largely agree on who is in the category, but each mentions "
    "those brands at a different rate. A single-engine number won't stand in "
    "for the others.")

DISCARDED_INTRO = "Each of these looked like a headline for about a day."

DISCARDED = [
    ("Content farms get cited more than brands' own sites",
     "A manual check of five flagged domains found a program-matching tool, "
     "a tutoring service and a legitimate developer academy. Source labels "
     "like \"content farm\" are judgment calls, so they need spot-checking "
     "before you build a finding on them."),
    ("Reddit barely registers as a source",
     "Community sites were under 1% of retrieved sources. That's consistent "
     "with the widely reported drop in Reddit citations in ChatGPT since "
     "August 2026, and it shows the drop still visible here in October."),
    ("API answers miss what real users see",
     "Nothing in this data tests that, because every answer came through an "
     "API. It belongs in the limits section, and it's why some tracking "
     "tools collect from the chat interface instead."),
    ("Perplexity always searches; Gemini rarely does",
     "Perplexity grounded 90 of 90 answers and Gemini 23 of 90, but my setup "
     "gave Perplexity an explicit search instruction and gave Gemini none. "
     "Anyone replicating this should configure every engine the same way "
     "before comparing grounding."),
    ("Some brands are cited far more often than they're named",
     "That gap is real, but it comes from what \"citation\" means. Those are "
     "retrieved sources, not used ones (see \"A citation is not an "
     "endorsement\")."),
]

CHECKS = [
    ("Count your branded prompts, and track them separately",
     "Each one adds a near-guaranteed mention. Branded prompts measure "
     "accuracy and reputation, not visibility."),
    ("Tag every prompt by intent, and make commercial your headline metric",
     "One blended number hides a range from about 3% to 33%. Commercial "
     "prompts are where shortlists form."),
    ("Sample repeatedly, and report counts with intervals",
     "\"20% (55 of 270, 16% to 26%)\" tells a reader what they can conclude. "
     "\"20%\" invites them to rank."),
    ("Report each engine separately",
     "The same brand ranged from 7% to 23% across three engines."),
    ("Find out what \"citation\" means in your tool",
     "Retrieved and used are different numbers. Until you can separate them, "
     "call it a retrieval rate."),
]

LIMITS = [
    ("API-based collection",
     "People use the chat apps, which may retrieve and personalise "
     "differently."),
    ("One category, one day",
     "US coding bootcamps on 1 October 2026. Engines and their indexes keep "
     "changing."),
    ("Small branded sample in the study",
     "The 100% branded rate rests on 3 prompts (its interval runs 93% to "
     "100%). The tracker's 482 of 482 is stronger evidence, but it comes "
     "from one engine."),
    ("Gemini excluded from the figures",
     "It returned 67 of 90 answers without grounding, and its sources come "
     "back through a redirect format the parser doesn't resolve yet."),
    ("Retrieved, not used",
     "Source counts overstate how much any one domain shaped an answer."),
]

OPEN_NOTE = (
    "Everything is open: the code, the data and both prompt panels are on "
    "GitHub. config.yaml is the original tracker's panel and "
    "config.study.yaml is the neutral one. Put them side by side and you can "
    "see the whole finding in a few seconds.")

FACTS = [
    ("Prompts", "30, three of them branded"),
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


INTENT_LABELS = {"named": "Branded", "commercial": "Commercial",
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

def chart_bars(rows, width=760, row_h=52, aria="", scale_max=1.0,
               ticks=(0, 0.25, 0.5, 0.75, 1.0), pad_l=190) -> str:
    """Horizontal bars with optional interval whiskers. rows: dicts with
    label, sub, rate, and optionally ci (lo, hi)."""
    if not rows:
        return ""
    pad_r, pad_t, pad_b = 96, 14, 30
    plot_w = width - pad_l - pad_r
    height = pad_t + len(rows) * row_h + pad_b
    x = lambda v: pad_l + (v / scale_max) * plot_w          # noqa: E731
    p = [f'<svg viewBox="0 0 {width} {height}" role="img" '
         f'aria-label="{esc(aria)}" xmlns="http://www.w3.org/2000/svg">']
    for v in ticks:
        gx = x(v)
        p.append(f'<line x1="{gx:.1f}" y1="{pad_t}" x2="{gx:.1f}" '
                 f'y2="{height - pad_b}" stroke="{C["rule"]}" stroke-width="1"/>')
        p.append(f'<text x="{gx:.1f}" y="{height - pad_b + 18}" '
                 f'text-anchor="middle" font-size="12" fill="{C["grey"]}">'
                 f'{v:.0%}</text>')
    for i, r in enumerate(rows):
        y = pad_t + i * row_h + row_h / 2
        p.append(f'<text x="{pad_l - 14}" y="{y - 2 if r.get("sub") else y + 5}" '
                 f'text-anchor="end" font-size="15" font-weight="600" '
                 f'fill="{C["ink"]}">{esc(r["label"])}</text>')
        if r.get("sub"):
            p.append(f'<text x="{pad_l - 14}" y="{y + 15}" text-anchor="end" '
                     f'font-size="12" fill="{C["grey"]}">{esc(r["sub"])}</text>')
        tip = f'{esc(r["label"])}: {r["rate"]:.1%}'
        if r.get("ci"):
            tip += f' (95% interval {r["ci"][0]:.1%} to {r["ci"][1]:.1%})'
        p.append(f'<rect x="{pad_l}" y="{y - 10:.1f}" '
                 f'width="{max(x(r["rate"]) - pad_l, 2):.1f}" height="20" '
                 f'rx="3" fill="{C["teal"]}"><title>{tip}</title></rect>')
        end = r["rate"]
        if r.get("ci"):
            lo, up = r["ci"]
            end = up
            p.append(f'<line x1="{x(lo):.1f}" y1="{y:.1f}" x2="{x(up):.1f}" '
                     f'y2="{y:.1f}" stroke="{C["ink"]}" stroke-width="1.5"/>')
            for xx in (lo, up):
                p.append(f'<line x1="{x(xx):.1f}" y1="{y - 6:.1f}" '
                         f'x2="{x(xx):.1f}" y2="{y + 6:.1f}" '
                         f'stroke="{C["ink"]}" stroke-width="1.5"/>')
        label = "100%" if r.get("k") is not None and r["k"] == r["n"] \
            else f'{r["rate"]:.1%}'
        p.append(f'<text x="{x(end) + 10:.1f}" y="{y + 5:.1f}" font-size="15" '
                 f'font-weight="600" fill="{C["ink"]}">{label}</text>')
    p.append("</svg>")
    return "".join(p)


def any_brand_by_intent(db: str, cfg, **kw) -> list[dict]:
    """Share of answers that name at least one tracked brand, per intent,
    leaving out the comparative prompts (which name brands by construction)."""
    clause, params = _filters(**kw)
    conn = sqlite3.connect(db)
    q = (f"SELECT r.intent, COUNT(*), SUM(EXISTS(SELECT 1 FROM mentions m "
         f"WHERE m.response_id = r.id AND m.count > 0)) FROM responses r "
         f"WHERE {clause} GROUP BY r.intent")
    got = {i: (n, k or 0) for i, n, k in conn.execute(q, params)}
    conn.close()
    out = []
    for key in ("commercial", "transactional", "informational"):
        if key in got:
            n, k = got[key]
            out.append({"key": key, "label": INTENT_LABELS[key], "k": k,
                        "n": n, "rate": k / n if n else 0.0})
    return out


def engine_split(db: str, cfg, brand: str, **kw) -> list[dict]:
    """One brand's mention rate on unbranded prompts, per engine."""
    import re
    ent = next(e for e in cfg.all_entities if e.name == brand)
    names = {pr.id for pr in cfg.prompts
             if any(re.search(rf"\b{re.escape(a)}\b", pr.text, re.I)
                    for a in (ent.aliases or [ent.name]))}
    clause, params = _filters(**kw)
    conn = sqlite3.connect(db)
    q = (f"SELECT r.engine, r.prompt_id, (m.count > 0) FROM mentions m "
         f"JOIN responses r ON r.id = m.response_id WHERE m.entity = ? AND {clause}")
    tally: dict[str, list[int]] = {}
    for eng, pid, hit in conn.execute(q, [brand, *params]):
        if pid in names:
            continue
        t = tally.setdefault(eng, [0, 0])
        t[0] += int(hit); t[1] += 1
    conn.close()
    rows = [{"engine": e, "label": ENGINE_NAMES.get(e, e), "k": k, "n": n,
             "rate": k / n if n else 0.0} for e, (k, n) in tally.items()]
    rows.sort(key=lambda r: -r["rate"])
    return rows


CSS = f"""<style>
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
    font-size:clamp(32px,4.4vw,52px); line-height:1.04; font-weight:800;
    letter-spacing:-0.03em; margin:0 0 22px; max-width:22ch; text-wrap:balance;
  }}
  .standfirst, .hook {{ font-size:19px; line-height:1.55; margin:0; max-width:60ch; color:var(--ink); }}
  .hook {{ margin-top:16px; }}
  ul.bul {{ margin:0 0 18px; padding-left:20px; }}
  ul.bul li {{ margin-bottom:6px; }}
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
  aside {{
    position:-webkit-sticky; position:sticky; top:24px; align-self:start;
    margin-top:72px; max-height:calc(100vh - 40px); overflow-y:auto;
    scrollbar-width:thin; padding-right:2px;
  }}
  .mobile-bar {{
    display:none; position:-webkit-sticky; position:sticky; top:0; z-index:5;
    background:var(--ground); border-bottom:1px solid var(--rule);
    margin:0 -18px; padding:10px 18px; overflow-x:auto; white-space:nowrap;
    scrollbar-width:none;
  }}
  .mobile-bar::-webkit-scrollbar {{ display:none; }}
  .mobile-bar b {{ margin-right:10px; font-size:14px; }}
  .mobile-bar a {{
    display:inline-block; padding:5px 10px; border:1px solid var(--rule);
    border-radius:999px; font-size:13px; font-weight:500; text-decoration:none;
    color:var(--ink); background:var(--panel); margin-right:6px;
  }}
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
  .toc li {{ padding:4px 0; border-top:1px solid var(--rule); font-size:14px; line-height:1.3; }}
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
  .kicker {{ display:block; font-size:0.5em; font-weight:500; letter-spacing:-0.01em;
             line-height:1.3; color:var(--grey); margin-top:14px; max-width:36ch; }}
  .muted {{ color:var(--grey); font-weight:400; font-size:14px; }}
  pre.eq {{ background:var(--panel); border:1px solid var(--rule); border-radius:6px;
            padding:14px 16px; font-size:13px; line-height:1.5; overflow-x:auto; white-space:pre-wrap;
            margin:0 0 18px; }}
  code {{ font-size:0.9em; background:var(--panel); border:1px solid var(--rule);
          border-radius:4px; padding:1px 5px; }}
  table.design td.k {{ color:var(--grey); width:150px; }}
  table.guide td {{ vertical-align:top; }}
  .limits table {{ margin:0; }}
  .limits td.k {{ width:200px; }}
  footer code {{ font-size:0.85em; }}
  footer {{
    margin-top:56px; padding-top:18px; border-top:1px solid var(--rule);
    font-size:14.5px; color:var(--grey);
  }}

  @media (max-height:860px) {{ .facts {{ display:none; }} }}
  @media (max-width:1040px) {{
    .layout {{ grid-template-columns:minmax(0,1fr) 232px; column-gap:40px; }}
  }}
  @media (max-width:860px) {{
    .layout {{ grid-template-columns:1fr; }}
    aside {{ position:static; margin-top:40px; max-height:none; overflow:visible; }}
    .facts {{ display:block; }}
    .mobile-bar {{ display:block; }}
  }}
  @media (max-width:640px) {{
    body {{ font-size:16px; }}
    .shell {{ padding:0 18px 72px; }}
    .hero {{ padding-top:40px; }}
    .standfirst, .hook {{ font-size:17.5px; }}
    h2 {{ font-size:26px; }}
    .pull {{ font-size:20px; }}
    section {{ padding-top:44px; }}
  }}
</style>"""


def build_html(d: dict) -> str:
    rows = d["brands"]
    pooled = d["pooled_intent"]
    m = d["monitor"]
    sp = d["study_split"]

    # --- hero split bar: the 61 points, decomposed ------------------------
    named_share = m["named_answers"] / m["total_answers"]
    other_share = m["other_answers"] / m["total_answers"]
    named_rate = m["named_hits"] / m["named_answers"]
    other_rate = m["other_hits"] / m["other_answers"]
    named_pts = named_share * named_rate * 100
    other_pts = other_share * other_rate * 100
    total_pts = m["total_hits"] / m["total_answers"] * 100

    def pct(r):
        return "100%" if r["k"] == r["n"] else f'{r["rate"]:.1%}'

    # --- the short version ------------------------------------------------
    short_rows = "".join(
        f'<tr><td><b>{esc(r["label"])}</b></td>'
        f'<td class="t">"{esc(r["example"])}"</td>'
        f'<td class="n"><b>{pct(r)}</b> <span class="muted">({r["k"]:,} of {r["n"]:,})</span></td></tr>'
        for r in pooled)

    # --- the 61% ----------------------------------------------------------
    def row(label, prompts, n, k, strong=False):
        tag = "b" if strong else "span"
        return (f'<tr><td><{tag}>{esc(label)}</{tag}></td><td class="n">{prompts}</td>'
                f'<td class="n">{n}</td><td class="n"><{tag}>{k} ({k / n:.0%})</{tag}></td></tr>')
    monitor_rows = (
        row("Branded prompts", m["named_prompts"], m["named_answers"], m["named_hits"])
        + row("Unbranded prompts", m["total_prompts"] - m["named_prompts"], m["other_answers"], m["other_hits"])
        + row("Full panel, as reported", m["total_prompts"], m["total_answers"], m["total_hits"], strong=True))
    arith_rows = (
        f'<tr><td>Branded prompts</td><td class="n">{m["named_answers"]} of {m["total_answers"]} ({named_share:.1%})</td>'
        f'<td class="n">× {named_rate:.0%}</td><td class="n"><b>{named_pts:.1f}</b></td></tr>'
        f'<tr><td>Unbranded prompts</td><td class="n">{m["other_answers"]} of {m["total_answers"]} ({other_share:.1%})</td>'
        f'<td class="n">× {other_rate:.1%}</td><td class="n"><b>{other_pts:.1f}</b></td></tr>'
        f'<tr><td><b>Reported mention rate</b></td><td></td><td></td><td class="n"><b>{total_pts:.1f}</b></td></tr>')

    # --- across the category ---------------------------------------------
    design_rows = "".join(f'<tr><td class="k">{esc(k)}</td><td>{esc(v)}</td></tr>'
                          for k, v in d["study_design"])
    anyb = d["any_brand"]
    any_rows = "".join(
        f'<tr><td>{esc(r["label"])}</td><td class="n"><b>{r["rate"]:.0%}</b> '
        f'<span class="muted">({r["k"]} of {r["n"]})</span></td></tr>' for r in anyb)
    study_other_rate = sp["other"][0] / max(sp["other"][1], 1)
    study_all_rate = (sp["named"][0] + sp["other"][0]) / max(sp["named"][1] + sp["other"][1], 1)
    same_rows = (
        f'<tr><td>Original tracker, full panel (Claude, August)</td><td class="n">{m["total_prompts"]}</td>'
        f'<td class="n">{m["total_answers"]}</td><td class="n"><b>{total_pts:.0f}%</b></td></tr>'
        f'<tr><td>Original tracker, unbranded prompts only</td><td class="n">{m["total_prompts"] - m["named_prompts"]}</td>'
        f'<td class="n">{m["other_answers"]}</td><td class="n"><b>{other_rate:.0%}</b></td></tr>'
        f'<tr><td>This study, unbranded prompts only ({esc(d["engines_label"])}, October)</td>'
        f'<td class="n">{sp["other_prompts"]}</td><td class="n">{sp["other"][1]}</td>'
        f'<td class="n"><b>{study_other_rate:.0%}</b></td></tr>'
        f'<tr><td>This study, full panel</td><td class="n">{sp["named_prompts"] + sp["other_prompts"]}</td>'
        f'<td class="n">{sp["named"][1] + sp["other"][1]}</td><td class="n"><b>{study_all_rate:.0%}</b></td></tr>')
    same_outro = d["same_brand_outro"].format(other=f"{study_other_rate:.0%}")

    # --- intent guide -----------------------------------------------------
    guide_rows = "".join(
        f'<tr><td><b>{esc(a)}</b><br><span class="ex">"{esc(b)}"</span></td>'
        f'<td>{esc(c)}</td><td>{("<b>" + esc(e) + "</b>") if a == "Commercial" else esc(e)}</td></tr>'
        for a, b, c, e in d["intent_guide"])

    # --- citation ---------------------------------------------------------
    gap_rows = "".join(f'<tr><td>{esc(k)}</td><td class="n"><b>{v}</b></td></tr>'
                       for k, v in d["citation_gap"])
    can_rows = "".join(f'<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>'
                       for a, b in d["retrieval_can"])

    # --- engines ----------------------------------------------------------
    eng = d["engine_split"]
    eng_rows = "".join(
        f'<tr><td>{esc(r["label"])}</td><td class="n"><b>{r["rate"]:.1%}</b> '
        f'<span class="muted">({r["k"]} of {r["n"]})</span></td></tr>' for r in eng)
    pair_names = {"anthropic|openai": "Claude and ChatGPT",
                  "anthropic|perplexity": "Claude and Perplexity",
                  "openai|perplexity": "ChatGPT and Perplexity"}
    pairs = d["cross_model"].get("pairwise", {})
    jac_rows = "".join(
        f'<tr><td>{esc(pair_names.get(k, k))}</td><td class="n"><b>{v:.2f}</b></td></tr>'
        for k, v in sorted(pairs.items(), key=lambda kv: -kv[1]))

    # --- lists ------------------------------------------------------------
    discarded = "".join(f'<li><b>{esc(t)}</b><span>{esc(w)}</span></li>'
                        for t, w in d["discarded"])
    checks = "".join(f'<li><b>{esc(t)}</b><span>{esc(w)}</span></li>'
                     for t, w in d["checks"])
    limits = "".join(f'<tr><td class="k">{esc(a)}</td><td>{esc(b)}</td></tr>'
                     for a, b in d["limits"])

    links = "".join(f'<li><a href="{esc(u)}">{esc(t)}</a></li>'
                    for t, u in d["author"]["links"])
    bar_links = "".join(f'<a href="{esc(u)}">{esc(t)}</a>'
                        for t, u in d["author"]["links"])
    facts = "".join(f'<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>'
                    for k, v in d["facts"])
    toc = [("short", "The short version"),
           ("why", "Why this is worth measuring carefully"),
           ("sixty-one", "Where the 61% came from"),
           ("category", "Does it hold across a category?"),
           ("intents", "What each prompt intent measures"),
           ("citation", "A citation is not an endorsement"),
           ("cluster", "A cluster, not a ranking"),
           ("engines", "Which engine you ask matters too"),
           ("discarded", "5 findings that didn't survive"),
           ("checks", "5 checks before you trust a score"),
           ("limits", "What this study can't tell you")]
    toc_html = "".join(f'<li><a href="#{i}">{esc(t)}</a></li>' for i, t in toc)

    intent_chart = chart_bars(
        [{"label": r["label"], "sub": f'{r["k"]:,} of {r["n"]:,}', "rate": r["rate"],
          "ci": r["ci"], "k": r["k"], "n": r["n"]} for r in pooled],
        aria="Share of answers naming a given brand, by prompt intent")
    any_chart = chart_bars(
        [{"label": r["label"], "sub": f'{r["k"]} of {r["n"]}', "rate": r["rate"],
          "k": r["k"], "n": r["n"]} for r in anyb],
        aria="Share of answers naming at least one tracked brand, by prompt intent")
    engine_chart = chart_bars(
        [{"label": r["label"], "sub": f'{r["k"]} of {r["n"]}', "rate": r["rate"],
          "k": r["k"], "n": r["n"]} for r in eng],
        aria="One brand's mention rate on unbranded prompts, by engine",
        scale_max=0.5, ticks=(0, 0.1, 0.2, 0.3, 0.4, 0.5), row_h=46)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(d["title"])}</title>
<meta name="description" content="{esc(d["standfirst"])}">
<meta property="og:title" content="{esc(d["title"])}">
<meta property="og:description" content="{esc(d["standfirst"])}">
<meta property="og:image" content="https://ai-visibility-audit-rho.vercel.app/study/prompt-effect.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="author" content="{esc(d["author"]["name"])}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400;1,500&display=swap" rel="stylesheet">
{CSS}
</head>
<body>
<div class="shell">
<div class="layout">
<main>
<div class="mobile-bar"><b>{esc(d["author"]["name"])}</b>{bar_links}</div>

<header class="hero">
  <h1>{esc(d["title_main"])} <span class="kicker">{esc(d["title_sub"])}</span></h1>
  <p class="standfirst">{esc(d["standfirst"])}</p>
  <p class="hook">{esc(d["hook"])}</p>
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
    <figcaption>My first tracker's final weekly run: {m["total_answers"]} answers,
    {m["total_prompts"]} prompts, {m["named_prompts"]} of them naming the brand.</figcaption>
  </figure>
</header>

<section id="short">
<h2>The short version</h2>
<p>{esc(d["short_intro"])}</p>
<figure>
  {intent_chart}
  <figcaption>Ten coding bootcamps, three engines ({esc(d["engines_label"])}),
  {d["n"]} answers. Each row checks every brand against every answer to a prompt of
  that intent. Whiskers are 95% Wilson confidence intervals; no two rows overlap.</figcaption>
</figure>
<table>
  <thead><tr><th>Prompt intent</th><th>Example</th><th class="n">Answers naming a given brand</th></tr></thead>
  <tbody>{short_rows}</tbody>
</table>
<p class="pull">{esc(d["short_outro"])}</p>
</section>

<section id="why">
<h2>Why this is worth measuring carefully</h2>
<p>{esc(d["why_1"])}</p>
<p>{esc(d["why_2"])}</p>
</section>

<section id="sixty-one">
<h2>Where the 61% came from</h2>
<p>{esc(d["sixty_one_1"])}</p>
<figure>
  <img src="monitor-before.png" alt="The original tracker's dashboard, reporting a 61% mention rate for one brand across 147 Claude answers." loading="lazy">
  <figcaption>The tracker's dashboard as published in August 2026.</figcaption>
</figure>
<table>
  <thead><tr><th>Tracker's final run (Claude, August 2026)</th><th class="n">Prompts</th>
  <th class="n">Answers</th><th class="n">Answers naming the brand</th></tr></thead>
  <tbody>{monitor_rows}</tbody>
</table>
<p>{esc(d["sixty_one_2"])}</p>
<h3>The arithmetic, and why it holds</h3>
<p>{esc(d["arith_1"])}</p>
<ul class="bul">{"".join(f"<li><b>{esc(t)}</b>: {esc(x)}</li>" for t, x in d["arith_terms"])}</ul>
<p>{esc(d["arith_2"])}</p>
<pre class="eq">mention rate =
    (share of answers from branded prompts)   × (branded rate)
  + (share of answers from unbranded prompts) × (unbranded rate)</pre>
<p>{esc(d["arith_3"])}</p>
<table>
  <thead><tr><th></th><th class="n">Share of answers</th><th class="n">× Rate</th><th class="n">= Points</th></tr></thead>
  <tbody>{arith_rows}</tbody>
</table>
<ul class="bul">{"".join(f"<li>{esc(x)}</li>" for x in d["arith_read"])}</ul>
<p class="pull">{esc(d["arith_5"])}</p>
</section>

<section id="category">
<h2>Does it hold across a category?</h2>
<p>{esc(d["category_intro"])}</p>
<table class="design">
  <tbody>{design_rows}</tbody>
</table>
<p>{esc(d["category_held"])}</p>
<p>{esc(d["category_mech_intro"])}</p>
<figure>
  {any_chart}
  <figcaption>Share of answers that named at least one of the ten brands, by prompt
  intent. Comparative prompts are left out because they name brands by construction.</figcaption>
</figure>
<p>{esc(d["category_mech"])}</p>
<p>{esc(d["same_brand_intro"])}</p>
<table>
  <thead><tr><th></th><th class="n">Prompts</th><th class="n">Answers</th><th class="n">Brand named</th></tr></thead>
  <tbody>{same_rows}</tbody>
</table>
<p>{esc(same_outro)}</p>
</section>

<section id="intents">
<h2>What each prompt intent actually measures</h2>
<p>{esc(d["intent_guide_intro"])}</p>
<table class="guide">
  <thead><tr><th>Prompt intent</th><th>What it measures</th><th>Use it for</th></tr></thead>
  <tbody>{guide_rows}</tbody>
</table>
</section>

<section id="citation">
<h2>A citation is not an endorsement</h2>
<p>{esc(d["citation_1"])}</p>
<p>{esc(d["citation_2"])}</p>
<table>
  <tbody>{gap_rows}</tbody>
</table>
<p class="pull">{esc(d["citation_3"])}</p>
<table class="guide">
  <thead><tr><th>A retrieval rate can tell you</th><th>It can't tell you</th></tr></thead>
  <tbody>{can_rows}</tbody>
</table>
<p>{esc(d["citation_4"])}</p>
</section>

<section id="cluster">
<h2>The brands form a cluster, not a ranking</h2>
<p class="sub">Mention rate across every prompt, with its 95% confidence interval.</p>
<figure>
  {chart_intervals(rows)}
  <figcaption>The bar is the range the true rate is very likely to fall in. The tick
  is what was measured. Where two bars overlap, the data can't say which brand is ahead.</figcaption>
</figure>
<p>{esc(d["cluster_para"])}</p>
</section>

<section id="engines">
<h2>Which engine you ask matters too</h2>
<p>{esc(d["engine_intro"])}</p>
<figure>
  {engine_chart}
</figure>
<p>{esc(d["engine_jaccard_intro"])}</p>
<table>
  <thead><tr><th>Engine pair</th><th class="n">Jaccard overlap</th></tr></thead>
  <tbody>{jac_rows}</tbody>
</table>
<p>{esc(d["engine_outro"])}</p>
</section>

<section id="discarded">
<h2>5 findings that didn't survive</h2>
<p class="sub">{esc(d["discarded_intro"])}</p>
<ol class="list">{discarded}</ol>
</section>

<section id="checks">
<h2>5 checks before you trust any AI visibility score</h2>
<ol class="list checks">{checks}</ol>
</section>

<div class="limits" id="limits">
  <h2>What this study can't tell you</h2>
  <table class="design"><tbody>{limits}</tbody></table>
</div>

<footer>
  <p>Everything is open: the code, the data and both prompt panels are on
  <a href="https://github.com/tkaplish888-alt/ai-visibility-audit">GitHub</a>.
  <code>config.yaml</code> is the original tracker's panel and
  <code>config.study.yaml</code> is the neutral one. Put them side by side and
  you can see the whole finding in a few seconds.<br>
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
    n_prompts = conn.execute(
        f"SELECT COUNT(DISTINCT r.prompt_id) FROM responses r WHERE {clause}",
        params).fetchone()[0]
    conn.close()

    label = ", ".join(ENGINE_NAMES.get(e, e) for e in sorted(engines))

    return {
        "title": TITLE, "title_main": TITLE_MAIN, "title_sub": TITLE_SUB,
        "standfirst": STANDFIRST, "hook": HOOK, "author": AUTHOR, "facts": FACTS,
        "n": n, "n_citations": ncit, "n_prompts": n_prompts or len(cfg.prompts),
        "engines": engines, "engines_label": label,
        "window": ((lo[:10] if lo[:10] == hi[:10] else f"{lo[:10]} to {hi[:10]}") if lo else ""),
        "brands": rows,
        "pooled_intent": pooled_by_intent(db, cfg, **kw),
        "any_brand": any_brand_by_intent(db, cfg, **kw),
        "monitor": MONITOR,
        "study_split": brand_split(db, cfg, MONITOR["brand"], **kw),
        "engine_split": engine_split(db, cfg, MONITOR["brand"], **kw),
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
        "short_intro": SHORT_INTRO, "short_outro": SHORT_OUTRO,
        "why_1": WHY_1, "why_2": WHY_2,
        "sixty_one_1": SIXTY_ONE_1, "sixty_one_2": SIXTY_ONE_2,
        "arith_1": ARITH_1, "arith_terms": ARITH_TERMS, "arith_2": ARITH_2,
        "arith_3": ARITH_3, "arith_read": ARITH_READ, "arith_5": ARITH_5,
        "category_intro": CATEGORY_INTRO, "study_design": STUDY_DESIGN,
        "category_held": CATEGORY_HELD,
        "category_mech_intro": CATEGORY_MECH_INTRO,
        "category_mech": CATEGORY_MECH,
        "same_brand_intro": SAME_BRAND_INTRO, "same_brand_outro": SAME_BRAND_OUTRO,
        "intent_guide_intro": INTENT_GUIDE_INTRO, "intent_guide": INTENT_GUIDE,
        "citation_1": CITATION_1, "citation_2": CITATION_2,
        "citation_gap": CITATION_GAP, "citation_3": CITATION_3,
        "retrieval_can": RETRIEVAL_CAN, "citation_4": CITATION_4,
        "cluster_para": CLUSTER_PARA,
        "engine_intro": ENGINE_INTRO, "engine_jaccard_intro": ENGINE_JACCARD_INTRO,
        "engine_outro": ENGINE_OUTRO,
        "discarded_intro": DISCARDED_INTRO, "discarded": DISCARDED,
        "checks": CHECKS, "limits": LIMITS, "open_note": OPEN_NOTE,
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
