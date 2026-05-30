"""
eval_large.py — large-scale labeled test for the context router.

Standalone from cli.py's small EVAL_SET. ~60 hand-labeled prompts spanning:
  * generic / off-domain        -> retrieve NOTHING (gold = empty)
  * adversarial near-misses     -> mention a project word but are generic
  * specific technical (1 chunk)-> retrieve the single chunk that answers it
  * paraphrases of the above    -> same gold, different wording
  * broad "summarize X" prompts -> multi-chunk gold

Each gold set was assigned by answering the prompt and asking "what stored
fact would I actually need?". Run:

    python -m experiments.context_router.eval_large
    python -m experiments.context_router.eval_large --fails   # only show misses
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from .router import ContextRouter


@dataclass
class Case:
    prompt: str
    levels: set[str]
    gold: set[str] = field(default_factory=set)
    note: str = ""


NO_CTX = {"none", "tiny"}
NO_CTX_LOOSE = {"none", "tiny", "selected"}  # generic but a weak match is forgivable
CTX = {"selected", "full"}


CASES: list[Case] = [
    # ---- generic / general-knowledge: retrieve nothing -------------------
    Case("What does markup mean?", NO_CTX, note="generic def"),
    Case("What is a lifecycle hook?", NO_CTX, note="generic def"),
    Case("Who vs whom — which is correct here?", NO_CTX, note="grammar"),
    Case("Explain that more simply.", NO_CTX, note="meta/no-content"),
    Case("What's a good pasta recipe?", NO_CTX, note="off-domain"),
    Case("How do I boil an egg?", NO_CTX, note="off-domain"),
    Case("What's the capital of France?", NO_CTX, note="off-domain"),
    Case("Define recursion in one sentence.", NO_CTX, note="generic def"),
    Case("What does idempotent mean?", NO_CTX, note="generic def"),
    Case("Tell me a joke.", NO_CTX, note="off-domain"),
    Case("Convert 10 miles to kilometers.", NO_CTX, note="off-domain"),
    Case("What year did the internet start?", NO_CTX, note="off-domain"),

    # ---- adversarial near-misses: project word, but general knowledge ----
    Case("What does the word origin mean?", NO_CTX_LOOSE, note="origin=word"),
    Case("What does CORS stand for?", NO_CTX_LOOSE, note="CORS=def not fix"),
    Case("What is OAuth in general?", NO_CTX_LOOSE, note="OAuth=def"),
    Case("What is a git branch, conceptually?", NO_CTX_LOOSE, note="branch=def"),
    Case("What is a virtual environment in Python?", NO_CTX_LOOSE, note="venv=def"),
    Case("What does redaction mean?", NO_CTX_LOOSE, note="redact=def"),
    Case("What is an overlay in UI design?", NO_CTX_LOOSE, note="overlay=def"),
    Case("What is a thread in programming?", NO_CTX_LOOSE, note="thread=def"),

    # ---- Git: specific ---------------------------------------------------
    Case("I'm confused about origin/main again, explain my setup.",
         CTX, {"git-origin-main-001"}),
    Case("Why do I keep mixing up local and remote tracking branches?",
         CTX, {"git-origin-main-001"}),
    Case("What's the difference between git fetch and git pull for me?",
         CTX, {"git-fetch-pull-002"}),
    Case("How did I end up in a detached HEAD state?",
         CTX, {"git-fetch-pull-002"}),
    Case("How do I move my Linux clone onto the bugfix branch?",
         CTX, {"git-branch-switch-003"}),
    Case("Which command switches my clone to the bug fix branch?",
         CTX, {"git-branch-switch-003"}),

    # ---- PySide6 ---------------------------------------------------------
    Case("Why does PySide6 crash on Linux with a libxcb error?",
         CTX, {"pyside-linux-dll-010"}),
    Case("DLL load failed for PySide6 on Linux — what do I install?",
         CTX, {"pyside-linux-dll-010"}),
    Case("What did we migrate the overlay UI away from?",
         CTX, {"pyside-migration-011"}),
    Case("Which UI parts were moved to PySide6?",
         CTX, {"pyside-migration-011"}),
    Case("Why do long LLM calls have to run off the Qt main thread?",
         CTX, {"pyside-qthread-012"}),
    Case("What is the GenerationCounter used for?",
         CTX, {"pyside-qthread-012"}),

    # ---- Wisp overlay ----------------------------------------------------
    Case("How does the Wisp overlay capture ambient context?",
         CTX, {"wisp-arch-020"}),
    Case("What does context_fetcher.py actually do?",
         CTX, {"wisp-context-fetcher-021"}),
    Case("Where is the redacted JSON snapshot written?",
         CTX, {"wisp-context-fetcher-021"}),
    Case("How is the overlay triggered, and why cache the window?",
         CTX, {"wisp-hotkey-022"}),

    # ---- Supabase --------------------------------------------------------
    Case("My Supabase Edge Function throws a CORS error — how do I fix it?",
         CTX, {"supabase-cors-030"}),
    Case("How do I set Access-Control-Allow-Origin on my edge function?",
         CTX, {"supabase-cors-030"}),
    Case("Where do I set VITE_SUPABASE_URL?",
         CTX, {"supabase-env-031"}),
    Case("What is my Supabase project ref again?",
         CTX, {"supabase-env-031"}),
    Case("GitHub OAuth login through Supabase fails — what do I check?",
         CTX, {"supabase-oauth-032"}),
    Case("Where do I add the OAuth callback URL?",
         CTX, {"supabase-oauth-032"}),

    # ---- AI team workflow ------------------------------------------------
    Case("Why does my AI team workflow stall sometimes?",
         CTX, {"aiteam-workflow-040"}),
    Case("What is TaskJar?", CTX, {"aiteam-taskjar-041"}),
    Case("Which module persists the AI team's tasks between runs?",
         CTX, {"aiteam-taskjar-041"}),

    # ---- Win/Linux env ---------------------------------------------------
    Case("What platform differences do I deal with between dev and deploy?",
         CTX, {"env-winlinux-050"}),
    Case("Why does global-hotkey handling differ across platforms?",
         CTX, {"env-winlinux-050"}),
    Case("How do I run the test suite?", CTX, {"env-venv-051"}),
    Case("What command runs my unit tests?", CTX, {"env-venv-051"}),

    # ---- misc ------------------------------------------------------------
    Case("What caused stale results on browser back-navigation in my transit app?",
         CTX, {"routing-otp-060"}),
    Case("Which library did I use for transit routing?",
         CTX, {"routing-otp-060"}),
    Case("Why is my clipboard text being redacted?",
         CTX, {"clipboard-redact-061"}),
    Case("What gets stripped from clipboard text before it hits disk?",
         CTX, {"clipboard-redact-061"}),

    # ---- broad / multi-chunk --------------------------------------------
    Case("Tell me about the Wisp overlay architecture.", CTX,
         {"wisp-arch-020", "wisp-context-fetcher-021", "wisp-hotkey-022"},
         note="broad multi"),
    Case("Summarize what went wrong with my AI team workflow.", CTX,
         {"aiteam-workflow-040", "aiteam-taskjar-041"}, note="broad multi"),
    Case("Recap everything about my Supabase setup.", CTX,
         {"supabase-cors-030", "supabase-env-031", "supabase-oauth-032"},
         note="broad multi"),
    Case("What are all my recurring Git pain points?", CTX,
         {"git-origin-main-001", "git-fetch-pull-002", "git-branch-switch-003"},
         note="broad multi"),
]


def _prf(selected: list[str], gold: set[str]) -> tuple[float, float, float]:
    sel = set(selected)
    if not gold:
        return (1.0 if not sel else 0.0, 1.0, 1.0 if not sel else 0.0)
    if not sel:
        return (1.0, 0.0, 0.0)
    tp = len(sel & gold)
    p = tp / len(sel)
    r = tp / len(gold)
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def _route_fn(router: ContextRouter, use_ladder: bool):
    return router.route_ladder if use_ladder else router.route


def _eval(router: ContextRouter, use_ladder: bool):
    """Run all cases under one routing fn; return per-case results + aggregates."""
    route = _route_fn(router, use_ladder)
    rows = []  # (case, res, p, r, f, good_level, is_fail)
    level_ok = 0
    ctx_cases = noise_cases = noise_clean = 0
    cp = cr = cf = 0.0
    for c in CASES:
        res = route(c.prompt)
        good = res.context_level in c.levels
        p, r, f = _prf(res.selected_chunk_ids, c.gold)
        level_ok += good
        if c.gold:
            ctx_cases += 1
            cp += p; cr += r; cf += f
        else:
            noise_cases += 1
            noise_clean += 1 if not res.selected_chunk_ids else 0
        missed = c.gold - set(res.selected_chunk_ids)
        extra = set(res.selected_chunk_ids) - c.gold
        is_fail = (not good) or (c.gold and (missed or extra)) or \
                  (not c.gold and res.selected_chunk_ids)
        rows.append((c, res, p, r, f, good, is_fail))
    n = len(CASES)
    agg = {
        "n": n,
        "level_ok": level_ok,
        "level_acc": level_ok / n,
        "ctx_cases": ctx_cases,
        "P": cp / ctx_cases if ctx_cases else 0.0,
        "R": cr / ctx_cases if ctx_cases else 0.0,
        "F1": cf / ctx_cases if ctx_cases else 0.0,
        "noise_clean": noise_clean,
        "noise_cases": noise_cases,
        "fails": sum(1 for r in rows if r[6]),
    }
    return rows, agg


def _print_rows(rows, show_only_fails: bool) -> None:
    for c, res, p, r, f, good, is_fail in rows:
        if show_only_fails and not is_fail:
            continue
        print(f"{'ok' if good else 'XX':>4} {p:>5.2f} {r:>5.2f} {f:>5.2f}  {c.prompt}")
        missed = c.gold - set(res.selected_chunk_ids)
        extra = set(res.selected_chunk_ids) - c.gold
        if not good:
            print(f"      -> level={res.context_level} expected {sorted(c.levels)}")
        if missed:
            print(f"      -> MISSED: {sorted(missed)}")
        if extra and c.gold:
            print(f"      -> EXTRA: {sorted(extra)}")
        if extra and not c.gold:
            print(f"      -> NOISE: {sorted(extra)}")


def _print_agg(label: str, agg: dict) -> None:
    print(f"[{label}] level_acc={agg['level_ok']}/{agg['n']} "
          f"({agg['level_acc']:.0%})  P={agg['P']:.2f} R={agg['R']:.2f} "
          f"F1={agg['F1']:.2f}  noise_clean={agg['noise_clean']}/{agg['noise_cases']}  "
          f"fails={agg['fails']}/{agg['n']}")


def run(show_only_fails: bool = False, use_ladder: bool = False) -> int:
    router = ContextRouter()
    pipeline = "ladder (legacy)" if use_ladder else "additive (new)"
    print(f"Embedder: {router.embedder.name} | chunks: {len(router.chunks)} "
          f"| pipeline: {pipeline} | cases: {len(CASES)}\n")
    print(f"{'lvl':>4} {'P':>5} {'R':>5} {'F1':>5}  prompt")
    print("-" * 92)
    rows, agg = _eval(router, use_ladder)
    _print_rows(rows, show_only_fails)
    print("-" * 92)
    _print_agg(pipeline, agg)
    return 0 if not agg["fails"] else 1


def run_compare() -> int:
    router = ContextRouter()
    print(f"Embedder: {router.embedder.name} | chunks: {len(router.chunks)} "
          f"| comparing LADDER vs ADDITIVE | cases: {len(CASES)}\n")
    ladder_rows, ladder_agg = _eval(router, use_ladder=True)
    add_rows, add_agg = _eval(router, use_ladder=False)

    # Per-case diff: only show cases where verdict changed.
    print("Per-case diff (only cases where the two pipelines disagree):\n")
    print(f"{'prompt':<60}  {'ladder':<24}  {'additive':<24}")
    print("-" * 112)
    flipped_better = flipped_worse = unchanged_fail = unchanged_ok = 0
    for (c, lres, _lp, _lr, _lf, _lg, lfail), (_, ares, _ap, _ar, _af, _ag, afail) in zip(
        ladder_rows, add_rows
    ):
        l_brief = f"{lres.context_level}:{len(lres.selected_chunk_ids)}c"
        a_brief = f"{ares.context_level}:{len(ares.selected_chunk_ids)}c"
        if lfail and not afail:
            flipped_better += 1
            tag = "FIXED   "
        elif not lfail and afail:
            flipped_worse += 1
            tag = "REGRESS "
        elif lfail and afail:
            unchanged_fail += 1
            tag = "still-X "
        else:
            unchanged_ok += 1
            continue  # don't spam unchanged passes
        prompt = c.prompt if len(c.prompt) <= 58 else c.prompt[:55] + "..."
        print(f"{tag}{prompt:<58}  {l_brief:<24}  {a_brief:<24}")

    print()
    print(f"FIXED by additive:   {flipped_better}")
    print(f"REGRESSED:           {flipped_worse}")
    print(f"Still failing both:  {unchanged_fail}")
    print(f"Already passing:     {unchanged_ok}")
    print()
    _print_agg("ladder  ", ladder_agg)
    _print_agg("additive", add_agg)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval_large", description=__doc__)
    ap.add_argument("--fails", action="store_true", help="show only failing cases")
    ap.add_argument("--ladder", action="store_true",
                    help="use the legacy ladder router instead of the additive one")
    ap.add_argument("--compare", action="store_true",
                    help="run both pipelines and show per-case diff + aggregates")
    args = ap.parse_args(argv)
    if args.compare:
        return run_compare()
    return run(show_only_fails=args.fails, use_ladder=args.ladder)


if __name__ == "__main__":
    sys.exit(main())
