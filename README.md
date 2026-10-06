# AEO tracker

Asks AI answer engines (Claude, ChatGPT, Perplexity, Gemini) the same questions
about coding bootcamps, then records **which bootcamps they name** and **which
websites the search step returned**. Everything lands in SQLite, raw payloads
included, so you can re-analyse it without paying for another run.

It started as a weekly brand monitor for one bootcamp's marketing team. It
became a study of why that monitor's headline number was wrong.

## What the study found

**Prompt composition sets the visibility number before the engine says a word.**

When a prompt names a brand, the answer names that brand. Every time. In 54 of
54 answers across three engines in this study, and in 482 of 482 answers across
six weekly runs of the original monitor. Zero exceptions. The further a prompt
gets from naming anyone, the less often any brand appears at all.

Pooled across all ten brands and three engines (ChatGPT, Claude, Perplexity),
holding collection window and sample size constant:

| Prompt type | Example | Answers naming a given brand |
|---|---|---|
| Names the brand | "Flatiron School vs General Assembly, which is better?" | 100% (54 of 54) |
| Commercial | "Best coding bootcamp for a career changer?" | 32.9% (355 of 1,080) |
| Transactional | "How do I pay for a bootcamp I can't afford upfront?" | 14.4% (39 of 270) |
| Informational | "Are coding bootcamps still worth it in 2026?" | 2.6% (28 of 1,080) |

Each cell is brand-answer pairs: ten brands checked against every answer to a
prompt of that type. 95% Wilson intervals do not overlap between any two rows.
Informational answers mostly name no bootcamp at all (85% of them), which is the
mechanism: engines answer "is it worth it" without recommending anyone.

**What that did to the original monitor.** Its dashboard reported a 61% mention
rate for Flatiron School. In that run, 12 of the 30 prompts named Flatiron. On
those prompts: 57 of 57. On the other 18 prompts, same engine, same week: 33 of
90, or 37%. The 61% was 39 points of panel design and 22 points of brand. On a
neutral 30-prompt panel across three engines, the same brand measures 20.4%, and
14.7% once the two comparison prompts that name it are set aside.

So a visibility score is roughly:

```
(share of prompts naming the brand) × 100%  +  (share that don't) × (the brand's actual rate)
```

The first term is a configuration choice. Only the second term is the brand.
Audit the panel before trusting the number.

Full results, intervals, the per-engine split, limitations, and the findings
that were tested and discarded along the way: **[ai-visibility-audit-rho.vercel.app/study](https://ai-visibility-audit-rho.vercel.app/study/)**.

Collection: 360 answers (30 prompts × 4 engines × 3 samples), zero failures
after one resume, roughly $20 in API spend, 1 October 2026. Models:
`claude-sonnet-5`, `gpt-5-2025-08-07`, `perplexity/sonar`, `gemini-3.8-flash`.
Gemini is excluded from the headline figures (known issue 4). The original
monitor's database is not included in this repository; it was collected for a
former employer.

## What it measures

- **Mention rate**: share of answers that name a brand. Word-boundary matching
  against an alias list per brand (spaced, hyphenated and shortened forms).
- **Citation rate**: share of answers whose returned sources include the brand's
  own domain (subdomains count). Read known issue 2 before using it: for two of
  the four engines, "returned sources" means what the search step retrieved,
  not what the answer drew on.
- **Source mix**: what kinds of domains the returned sources come from (review
  aggregators, competitor content, owned sites, editorial, community, content
  farms). Same caveat.
- **Cross-engine agreement**: Jaccard overlap of the brand sets two engines
  produce for the same prompt. (Jaccard: the size of the overlap divided by the
  size of the union. 1.0 means identical sets.) Average here: 0.57.
- **Grounding**: whether an answer came back with any returned sources. A proxy
  for "the engine searched", not proof of where the answer came from.
- **Prompt intent**: every prompt carries a tag (informational, commercial,
  transactional, comparative). Every rate above can be cut by intent. That is
  the whole point.

Rates come with 95% Wilson confidence intervals, and the `study` command prints
a pairwise significance matrix. If two intervals overlap, treat the difference
as unproven at that sample size. In this study, Springboard is separable from
the brands below Hack Reactor and nothing else is separable from its
neighbours, so the brand table is not a ranking. It is a cluster with one brand
near the top of it.

## Known issues and limitations

Read these before quoting any number.

1. **API, not UI.** Answers come from provider APIs with web search or grounding
   switched on, not from the chat interfaces people actually use. Commercial
   tools such as Profound and Peec collect from the browser for exactly this
   reason. These results describe API behaviour.
2. **"Citation" means retrieved, not used.** Perplexity and Anthropic return the
   sources the search step retrieved. The answer may never draw on them: of the
   101 answers whose returned sources included nucamp.co, 60 never name Nucamp.
   Treat citation rate as a retrieval rate. For Anthropic, the sources the model
   actually used are attached to the text blocks of the raw response and could
   be re-parsed from `raw`. That has not been done.
3. **Source mix is a retrieval mix.** Follows from issue 2. It describes what the
   search step surfaced, not what shaped the answer.
4. **Gemini citations are unresolved.** Gemini returns every source under a
   redirect host (`vertexaisearch.cloud.google.com`); the real domain is only in
   the title field. Every Gemini citation therefore scores as unmatched, and
   Gemini is excluded from the headline figures. The fix is resolving titles to
   domains in `aeo/sources.py`, then re-parsing. (Its 67 of 90 ungrounded
   answers are real, not an artifact: their raw payloads contain no grounding
   data at all.)
5. **Citation URLs and titles were lost in a re-parse.** `aeo.reparse` rebuilt
   the citations table from the stored domain list rather than from `raw`, so
   every citation row carries only a domain. The full URLs are recoverable from
   `raw`.
6. **Grounding is confounded.** Perplexity was given an explicit search
   instruction during the run; Gemini was not. Do not compare grounding across
   engines from this data.
7. **Scope.** One category (US coding bootcamps), 30 prompts, three samples per
   prompt per engine, one collection window, one day. The brand-named row of the
   intent table rests on three prompts; its lower interval bound is 93%.

## Quick start

```bash
pip install -r requirements.txt

# See the whole pipeline work with no API keys:
python -m aeo.cli run --dry-run
python -m aeo.cli report
```

Then add real engines:

```bash
cp .env.example .env      # fill in the keys you have
set -a; source .env; set +a
python -m aeo.cli run
python -m aeo.cli report --engine openai --since 2026-07-01
```

Optional dashboard: `streamlit run dashboard.py`. Static HTML report:
`python -m aeo.report_html` (writes `public/index.html`).

### Running the study

The study uses its own config and database (`aeo_study.db`), kept separate from
the monitor's history. The database shipped here is the frozen v1 dataset.

```bash
python3 -m aeo.cli --config config.study.yaml smoke    # one call per engine; check citations > 0
python3 -m aeo.cli --config config.study.yaml run
python3 -m aeo.cli --config config.study.yaml study    # mention rates with intervals
python3 -m aeo.cli --config config.study.yaml sources  # returned-source mix, top domains
python3 analysis/study.py                              # writes public/study/index.html + results.json
```

Add `--exclude-engine gemini` to `study`, `sources` or `analysis/study.py` to
drop an engine from the figures. Its rows stay in the database, and `study.py`
reports it in its own section.

Re-parse stored answers after changing aliases or source classifications
(no API calls): `python -m aeo.reparse`. Schema upgrades: `python -m aeo.migrate`.

## The five pipeline stages

| Stage | File | What it does |
|---|---|---|
| 1. Config | `config.yaml`, `config.study.yaml`, `aeo/config.py` | Brands with their aliases and domains, the prompt panel with intent tags, runs per prompt, which engines to use. |
| 2. Runner | `aeo/runner.py` | Loops prompt → engine → repeat N times, with rate limiting, retries, failure logging and `--resume`. |
| 3. Adapters | `aeo/adapters/*` | One module per engine. Each returns the answer text, the returned source URLs and the raw payload in one common shape. A missing key skips that engine rather than crashing. |
| 4. Parsing | `aeo/parsing.py`, `aeo/sources.py` | Finds brand mentions (word-boundary matching), matches returned domains to brands by suffix, and classifies each domain by source type. |
| 5. Storage and stats | `aeo/storage.py`, `aeo/metrics.py`, `aeo/stats.py` | Append-only SQLite, raw payloads kept. Every view (monitor metrics, intervals, source mix, agreement, significance) is a query over it. |

`config.yaml` is the monitor configuration: one brand at the centre, a
competitor list, 35 prompts. `config.study.yaml` is the study configuration: ten
brands as peers, 30 prompts, only three of which name anyone. The difference
between those two files is the finding.

## Scheduling

`.github/workflows/aeo.yml` can run the monitor on a schedule and append a run
(add your keys as repo secrets). It is set to manual trigger in this repository.
An alerter (`aeo/alerts.py`) compares consecutive runs against thresholds and
emails on movement.

## Where to spend your time

The code is small on purpose. The value is in the **prompt panel and its intent
tags**, the **alias lists** and the **domain classifications** in
`aeo/sources.py`. Grow those; the code does not need to change.

If you only change one thing, audit the prompt panel. Count how many prompts
name the brand you are measuring. That number explains more of your visibility
score than anything the engines are doing.

## Provider APIs change

The adapters call live provider SDKs with web search or grounding switched on.
If one breaks, the only contract the rest of the system depends on is the shape
returned by `aeo/adapters/base.py`, so the fix stays inside that one adapter.
`python -m aeo.cli models` lists the model IDs your keys can reach.

Two breakages during this study, for the record: Perplexity retired its
chat-completions endpoint on 27 September 2026 and the adapter now uses the
Agent API with an explicit search tool; Gemini closed `gemini-2.5-pro` to new
API keys and the adapter now tries a list of model IDs in order.
