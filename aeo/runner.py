"""Stage 2 — Runner. Three nested loops: prompt -> engine -> run N times.

Non-determinism is handled by design: the same question asked twice can return
different answers, because models sample from a distribution and grounded
models see different search results moment to moment. One query is one
observation, not a measurement. We run N times and let the stats layer report
a rate with an interval around it.

WHAT CHANGED (study extension):
  * Failures are RECORDED, not skipped silently. A missing row used to be
    invisible; now it is a row in `failures` with the provider's error.
  * Resumability. If a run dies at call 200 of 360, restarting it with
    --resume skips everything already stored instead of re-paying for it.
  * Rate limiting between calls, so a long run does not trip provider limits.
  * Prompt IDs and intent tags flow through to storage.
  * Raw provider payloads are stored, which makes every later re-parse free.
"""
from __future__ import annotations

import time

from .adapters import get_adapter
from .config import Config
from .parsing import parse_response
from .storage import Store


def run_pipeline(cfg: Config, verbose: bool = True, resume_ts: str | None = None,
                 sleep_seconds: float = 1.0, max_retries: int = 2) -> str:
    store = Store(cfg.database)
    run_ts = resume_ts or store.now_ts()
    done = store.completed_keys(run_ts) if resume_ts else set()
    if resume_ts and verbose:
        print(f"Resuming run {run_ts}: {len(done)} calls already stored.")

    adapters = {name: get_adapter(name) for name in cfg.engines}
    for name, a in adapters.items():
        if not a.available() and verbose:
            print(f"  [skip] {name}: no API key set")

    owned = cfg.owned_domains
    stored = failed = skipped = 0

    for prompt in cfg.prompts:
        for name, adapter in adapters.items():
            if not adapter.available():
                continue
            for i in range(cfg.runs_per_prompt):
                if (prompt.id, name, i) in done:
                    skipped += 1
                    continue

                res = None
                for attempt in range(max_retries + 1):
                    res = adapter.run(prompt.text)
                    if res.ok:
                        break
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)

                if not res.ok:
                    failed += 1
                    store.add_failure(run_ts, name, res.error or "unknown",
                                      prompt=prompt.text, prompt_id=prompt.id,
                                      run_index=i)
                    if verbose:
                        print(f"  [fail] {name} {prompt.id}#{i}: {res.error}")
                    time.sleep(sleep_seconds)
                    continue

                mentions = parse_response(res.answer_text, res.cited_domains, cfg)
                store.add_response(
                    run_ts, prompt.text, name, i,
                    res.answer_text, res.cited_domains, mentions,
                    prompt_id=prompt.id, intent=prompt.intent,
                    model_version=res.model_version, raw=res.raw,
                    citations=res.citations,
                    truncated=_looks_truncated(res.answer_text),
                    owned_domains=owned,
                )
                stored += 1
                time.sleep(sleep_seconds)

        if verbose:
            print(f"  done: {prompt.id}  {prompt.text[:58]}")

    store.close()
    if verbose:
        print(f"\nRun {run_ts}: stored {stored}, failed {failed}, "
              f"skipped {skipped} (already had them).")
        if failed:
            print("  Inspect failures:  "
                  "sqlite3 aeo.db 'SELECT engine,error,COUNT(*) FROM failures "
                  f"WHERE run_ts=\"{run_ts}\" GROUP BY engine,error;'")
    return run_ts


def smoke_test(cfg: Config, n_prompts: int = 2, verbose: bool = True) -> bool:
    """Hit every configured engine once with a couple of prompts and report
    whether each returned text AND citations.

    Run this before the full study. A provider returning zero citations means
    grounding is off for that provider, and its data would be unusable. Finding
    that out on run day costs you the day.
    """
    ok_all = True
    print("Smoke test — checking text and grounding per engine\n")
    for name in cfg.engines:
        adapter = get_adapter(name)
        if not adapter.available():
            print(f"  {name:<12} NO KEY  ({adapter.env_key} not set)")
            ok_all = False
            continue
        texts, cites, errs = 0, 0, []
        for prompt in cfg.prompts[:n_prompts]:
            res = adapter.run(prompt.text)
            if not res.ok:
                errs.append(res.error or "unknown")
                continue
            if res.answer_text.strip():
                texts += 1
            cites += len(res.citations)
            time.sleep(1.0)
        status = "OK" if texts and cites else "PROBLEM"
        if status == "PROBLEM":
            ok_all = False
        print(f"  {name:<12} {status:<8} text:{texts}/{n_prompts}  citations:{cites}")
        if not cites and texts:
            print(f"               ^ answered but cited nothing. Grounding is "
                  f"probably off for {name}. Do not include it until fixed.")
        for e in errs[:2]:
            print(f"               error: {e[:120]}")
    print()
    return ok_all


def list_models() -> None:
    """Ask each provider which model IDs this key can actually use.

    Provider model names churn, and some are closed to new keys while still
    working for older ones, so the only reliable list is the one your own key
    returns. Use this when an adapter reports "all models failed".
    """
    import os

    print("Model IDs available to your keys\n")

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            ms = anthropic.Anthropic().models.list(limit=20)
            print("  anthropic:")
            for m in ms.data:
                print(f"    {m.id}")
        except Exception as e:
            print(f"  anthropic: {str(e)[:140]}")
    else:
        print("  anthropic: no key")

    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            ids = sorted(m.id for m in OpenAI().models.list().data
                         if m.id.startswith(("gpt-", "o")))
            print("  openai:")
            for i in ids[:25]:
                print(f"    {i}")
        except Exception as e:
            print(f"  openai: {str(e)[:140]}")
    else:
        print("  openai: no key")

    if os.environ.get("PERPLEXITY_API_KEY"):
        try:
            from openai import OpenAI
            c = OpenAI(api_key=os.environ["PERPLEXITY_API_KEY"],
                       base_url="https://api.perplexity.ai")
            print("  perplexity:")
            for m in c.models.list().data[:25]:
                print(f"    {m.id}")
        except Exception as e:
            print(f"  perplexity: {str(e)[:140]}")
    else:
        print("  perplexity: no key")

    if os.environ.get("GEMINI_API_KEY"):
        try:
            from google import genai
            print("  gemini:")
            for m in genai.Client().models.list():
                actions = getattr(m, "supported_actions", None) or []
                if not actions or "generateContent" in actions:
                    print(f"    {getattr(m, 'name', '')}")
        except Exception as e:
            print(f"  gemini: {str(e)[:140]}")
    else:
        print("  gemini: no key")
    print()


def _looks_truncated(text):
    # Imported lazily: a top-level import makes `python -m aeo.reparse` warn,
    # because the package __init__ would load reparse before runpy executes it.
    from .reparse import looks_truncated
    return looks_truncated(text)
