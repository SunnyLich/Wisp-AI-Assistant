"""
eval_vs_llm.py — compare the router's decision vs an LLM's (Claude's) decision.

The "LLM oracle" labels in CASES below are baked judgments: for each prompt I
(Claude) looked at the seed chunk list and decided what a careful retrieval
system *should* do — what context level, and which chunk IDs (if any). The
script then runs the router and reports where router and LLM disagree.

This is a *training signal* file: each disagreement is a tuning candidate. Run:

    python -m experiments.context_router.eval_vs_llm
    python -m experiments.context_router.eval_vs_llm --fails
    python -m experiments.context_router.eval_vs_llm --ladder
    python -m experiments.context_router.eval_vs_llm --confusion
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from .router import ContextRouter


LEVELS = ("none", "tiny", "selected", "full")
LEVEL_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}


@dataclass
class LLMCase:
    """One labelled case. `level` and `chunks` are what *I* (Claude) judged
    the right answer to be; `rationale` is the one-line reason."""

    prompt: str
    level: str                       # canonical LLM choice
    chunks: set[str] = field(default_factory=set)
    rationale: str = ""


# IDs available in the seed corpus (chunks.py):
#   git-origin-main-001, git-fetch-pull-002, git-branch-switch-003
#   pyside-linux-dll-010, pyside-migration-011, pyside-qthread-012
#   wisp-arch-020, wisp-context-fetcher-021, wisp-hotkey-022
#   supabase-cors-030, supabase-env-031, supabase-oauth-032
#   aiteam-workflow-040, aiteam-taskjar-041
#   env-winlinux-050, env-venv-051
#   routing-otp-060, clipboard-redact-061
#   generic-markup-900, generic-lifecycle-901, generic-whowhom-902


CASES: list[LLMCase] = [
    # ============ NONE: off-domain, no project signal =====================
    LLMCase("What's a good pasta recipe?", "none", set(), "off-domain"),
    LLMCase("How do I boil an egg?", "none", set(), "off-domain"),
    LLMCase("Tell me a joke.", "none", set(), "off-domain"),
    LLMCase("What's the capital of France?", "none", set(), "off-domain"),
    LLMCase("Convert 10 miles to kilometers.", "none", set(), "off-domain"),
    LLMCase("What year did the internet start?", "none", set(), "off-domain"),
    LLMCase("Recommend a book to read this weekend.", "none", set(), "off-domain"),
    LLMCase("Is it going to rain tomorrow?", "none", set(), "off-domain"),
    LLMCase("How many ounces in a cup?", "none", set(), "off-domain"),
    LLMCase("What time zone is Tokyo in?", "none", set(), "off-domain"),

    # ============ TINY: generic defs, meta, anaphoric follow-ups ==========
    LLMCase("What does markup mean?", "tiny", set(),
            "generic def; markup chunk exists but is a dictionary def, not project"),
    LLMCase("What is a lifecycle hook?", "tiny", set(), "generic def"),
    LLMCase("Who vs whom — which is correct here?", "tiny", set(), "grammar"),
    LLMCase("Define recursion in one sentence.", "tiny", set(), "generic def"),
    LLMCase("What does idempotent mean?", "tiny", set(), "generic def"),
    LLMCase("Explain that more simply.", "tiny", set(), "meta follow-up, no content"),
    LLMCase("Say it again.", "tiny", set(), "anaphoric follow-up"),
    LLMCase("Why is it slow?", "tiny", set(), "anaphoric, no antecedent"),
    LLMCase("Can you simplify the last answer?", "tiny", set(), "meta follow-up"),
    LLMCase("Define encapsulation.", "tiny", set(), "generic CS def"),
    LLMCase("What is REST in general?", "tiny", set(), "generic def"),
    LLMCase("What is HTTP?", "tiny", set(), "generic def"),
    LLMCase("Explain what a closure is.", "tiny", set(), "generic CS def"),
    LLMCase("What does API mean?", "tiny", set(), "generic def"),

    # ============ TINY: adversarial near-misses (project word, generic q) =
    LLMCase("What does the word origin mean?", "tiny", set(),
            "'origin' is project-y but query is dictionary"),
    LLMCase("What does CORS stand for?", "tiny", set(),
            "definition, not a fix request"),
    LLMCase("What is OAuth in general?", "tiny", set(), "definition"),
    LLMCase("What is a git branch, conceptually?", "tiny", set(), "definition"),
    LLMCase("What is a virtual environment in Python?", "tiny", set(), "definition"),
    LLMCase("What does redaction mean?", "tiny", set(), "definition"),
    LLMCase("What is an overlay in UI design?", "tiny", set(), "definition"),
    LLMCase("What is a thread in programming?", "tiny", set(), "definition"),
    LLMCase("What is a hotkey?", "tiny", set(), "definition"),
    LLMCase("What is markdown?", "tiny", set(), "definition"),
    LLMCase("What is a clipboard buffer?", "tiny", set(), "definition"),
    LLMCase("What is a callback URL in general?", "tiny", set(), "definition"),
    LLMCase("What is Qt?", "tiny", set(), "definition of framework"),

    # ============ SELECTED: single-chunk specific technical ===============
    LLMCase("I'm confused about origin/main again, explain my setup.",
            "selected", {"git-origin-main-001"}, "exact identifier origin/main"),
    LLMCase("Why do I keep mixing up local and remote tracking branches?",
            "selected", {"git-origin-main-001"}, "remote tracking branches phrase"),
    LLMCase("What's the difference between git fetch and git pull for me?",
            "selected", {"git-fetch-pull-002"}, "exact phrase fetch vs pull"),
    LLMCase("How did I end up in a detached HEAD state?",
            "selected", {"git-fetch-pull-002"}, "detached HEAD phrase"),
    LLMCase("How do I move my Linux clone onto the bugfix branch?",
            "selected", {"git-branch-switch-003"}, "specific procedure"),
    LLMCase("Which command switches my clone to the bug fix branch?",
            "selected", {"git-branch-switch-003"}, "paraphrase"),
    LLMCase("Why does PySide6 crash on Linux with a libxcb error?",
            "selected", {"pyside-linux-dll-010"}, "libxcb identifier"),
    LLMCase("DLL load failed for PySide6 on Linux — what do I install?",
            "selected", {"pyside-linux-dll-010"}, "exact phrase"),
    LLMCase("What did we migrate the overlay UI away from?",
            "selected", {"pyside-migration-011"}, "tkinter -> pyside6"),
    LLMCase("Which UI parts were moved to PySide6?",
            "selected", {"pyside-migration-011"}, "migration scope"),
    LLMCase("Why do long LLM calls have to run off the Qt main thread?",
            "selected", {"pyside-qthread-012"}, "qt main thread phrase"),
    LLMCase("What is the GenerationCounter used for?",
            "selected", {"pyside-qthread-012"}, "GenerationCounter identifier"),
    LLMCase("How does the Wisp overlay capture ambient context?",
            "selected", {"wisp-arch-020"}, "wisp identifier"),
    LLMCase("What does context_fetcher.py actually do?",
            "selected", {"wisp-context-fetcher-021"}, "context_fetcher.py identifier"),
    LLMCase("Where is the redacted JSON snapshot written?",
            "selected", {"wisp-context-fetcher-021"}, "redacted JSON snapshot phrase"),
    LLMCase("How is the overlay triggered, and why cache the window?",
            "selected", {"wisp-hotkey-022"}, "overlay triggered + cache"),
    LLMCase("My Supabase Edge Function throws a CORS error — how do I fix it?",
            "selected", {"supabase-cors-030"}, "supabase + CORS combo"),
    LLMCase("How do I set Access-Control-Allow-Origin on my edge function?",
            "selected", {"supabase-cors-030"}, "exact header name"),
    LLMCase("Where do I set VITE_SUPABASE_URL?",
            "selected", {"supabase-env-031"}, "VITE_SUPABASE_URL identifier"),
    LLMCase("What is my Supabase project ref again?",
            "selected", {"supabase-env-031"}, "project ref phrase"),
    LLMCase("GitHub OAuth login through Supabase fails — what do I check?",
            "selected", {"supabase-oauth-032"}, "github OAuth supabase"),
    LLMCase("Where do I add the OAuth callback URL?",
            "selected", {"supabase-oauth-032"}, "OAuth callback URL phrase"),
    LLMCase("Why does my AI team workflow stall sometimes?",
            "selected", {"aiteam-workflow-040"}, "AI team workflow phrase"),
    LLMCase("What is TaskJar?", "selected", {"aiteam-taskjar-041"},
            "TaskJar identifier — definition q but project-specific term"),
    LLMCase("Which module persists the AI team's tasks between runs?",
            "selected", {"aiteam-taskjar-041"}, "task_store / persistence"),
    LLMCase("What platform differences do I deal with between dev and deploy?",
            "selected", {"env-winlinux-050"}, "win/linux env"),
    LLMCase("Why does global-hotkey handling differ across platforms?",
            "selected", {"env-winlinux-050"}, "global hotkey + platform"),
    LLMCase("How do I run the test suite?", "selected", {"env-venv-051"},
            "test suite -> python -m unittest note"),
    LLMCase("What command runs my unit tests?",
            "selected", {"env-venv-051"}, "paraphrase"),
    LLMCase("What caused stale results on browser back-navigation in my transit app?",
            "selected", {"routing-otp-060"}, "bfcache + transit"),
    LLMCase("Which library did I use for transit routing?",
            "selected", {"routing-otp-060"}, "OpenTripPlanner"),
    LLMCase("Why is my clipboard text being redacted?",
            "selected", {"clipboard-redact-061"}, "clipboard redaction"),
    LLMCase("What gets stripped from clipboard text before it hits disk?",
            "selected", {"clipboard-redact-061"}, "redaction filter"),

    # ============ SELECTED/FULL: broad multi-chunk =======================
    LLMCase("Tell me about the Wisp overlay architecture.",
            "full", {"wisp-arch-020", "wisp-context-fetcher-021", "wisp-hotkey-022"},
            "broad: all three wisp chunks"),
    LLMCase("Summarize what went wrong with my AI team workflow.",
            "selected", {"aiteam-workflow-040", "aiteam-taskjar-041"},
            "two-chunk summary"),
    LLMCase("Recap everything about my Supabase setup.",
            "full", {"supabase-cors-030", "supabase-env-031", "supabase-oauth-032"},
            "broad: all three supabase chunks"),
    LLMCase("What are all my recurring Git pain points?",
            "full", {"git-origin-main-001", "git-fetch-pull-002", "git-branch-switch-003"},
            "broad: all three git chunks"),
    LLMCase("Walk me through everything PySide6-related in this project.",
            "full", {"pyside-linux-dll-010", "pyside-migration-011", "pyside-qthread-012"},
            "broad: all three pyside chunks"),
    LLMCase("Give me a tour of the whole Wisp app.",
            "full", {"wisp-arch-020", "wisp-context-fetcher-021", "wisp-hotkey-022"},
            "broad architecture tour"),

    # ============ Mixed / cross-topic =====================================
    LLMCase("How does the overlay's context fetcher interact with clipboard redaction?",
            "selected", {"wisp-context-fetcher-021", "clipboard-redact-061"},
            "cross-topic, two specific chunks"),
    LLMCase("Compare PySide6 thread cancellation with how the AI team handles stalls.",
            "selected", {"pyside-qthread-012", "aiteam-workflow-040"},
            "cross-topic, two specific chunks"),
    LLMCase("Why does the hotkey behave differently on Linux vs Windows?",
            "selected", {"wisp-hotkey-022", "env-winlinux-050"},
            "hotkey + platform cross"),
    LLMCase("How do I switch branches on my Linux box?",
            "selected", {"git-branch-switch-003"},
            "linux + branch — branch-switch chunk is the answer"),

    # ============ Paraphrases / synonyms ==================================
    LLMCase("CORS is blocking my Supabase function, help.",
            "selected", {"supabase-cors-030"}, "informal paraphrase"),
    LLMCase("Edge function won't talk to my frontend — CORS issue.",
            "selected", {"supabase-cors-030"}, "synonym-heavy paraphrase"),
    LLMCase("My Qt app freezes on Linux startup with some xcb thing.",
            "selected", {"pyside-linux-dll-010"}, "no PySide6 mention, just qt+xcb"),
    LLMCase("The intent overlay UI — what tech is it written in now?",
            "selected", {"pyside-migration-011"}, "intent overlay = pyside migration"),
    LLMCase("Where is the snapshot of redacted data dumped?",
            "selected", {"wisp-context-fetcher-021"}, "paraphrase of JSON snapshot"),
    LLMCase("My agents keep arguing and the planner gets stuck.",
            "selected", {"aiteam-workflow-040"}, "paraphrase of stall"),

    # ============ Ambiguous / weak — hard cases ===========================
    LLMCase("Why is it broken?", "tiny", set(),
            "no antecedent at all"),
    LLMCase("It's slow.", "tiny", set(), "no antecedent"),
    LLMCase("Help.", "none", set(), "no signal"),
    LLMCase("hi", "none", set(), "greeting"),
    LLMCase("thanks", "none", set(), "ack"),
    LLMCase("ok continue", "tiny", set(), "continuation, no content"),
    LLMCase("What did I ask before?", "tiny", set(),
            "meta — needs conversation, not stored ctx"),
    LLMCase("Show me the code.", "tiny", set(),
            "underspecified — which code?"),

    # ============ Adversarial: project-shaped but generic =================
    LLMCase("What is a CORS preflight request?",
            "tiny", set(),
            "CORS appears in chunk but query is generic mechanism Q"),
    LLMCase("What is the purpose of a JSON snapshot, in general?",
            "tiny", set(),
            "phrase appears but query is generic"),
    LLMCase("Explain detached HEAD generally, not for my repo.",
            "tiny", set(),
            "even with detached HEAD, framing is generic"),
    LLMCase("In general, why do GUI apps use worker threads?",
            "tiny", set(),
            "generic worker-threads question"),
    LLMCase("What's an edge function?", "tiny", set(),
            "definition of edge function (generic)"),

    # ============ Specific but with weak surface overlap ==================
    LLMCase("How do I cancel a stale query mid-flight in the chat UI?",
            "selected", {"pyside-qthread-012"},
            "matches GenerationCounter / stale queries idea"),
    LLMCase("My multi-agent setup deadlocks — any notes?",
            "selected", {"aiteam-workflow-040"},
            "deadlock ~ stall"),
    LLMCase("How do I keep tasks across restarts?",
            "selected", {"aiteam-taskjar-041"}, "persistence between runs"),

    # ============ Repeats with synonym swaps to test robustness ===========
    LLMCase("origin/main confuses me, what's my mental model?",
            "selected", {"git-origin-main-001"}, "identifier"),
    LLMCase("fetch vs pull — refresh me on which one I want.",
            "selected", {"git-fetch-pull-002"}, "phrase"),
    LLMCase("libxcb-cursor0 — do I need that on Linux?",
            "selected", {"pyside-linux-dll-010"}, "exact id"),
    LLMCase("VITE_SUPABASE_URL keeps coming back undefined.",
            "selected", {"supabase-env-031"}, "identifier"),
    LLMCase("TaskJar isn't loading old tasks.",
            "selected", {"aiteam-taskjar-041"}, "identifier"),
    LLMCase("OpenTripPlanner question for you.",
            "selected", {"routing-otp-060"}, "identifier"),

    # ============ Definitional questions about project-specific terms =====
    LLMCase("What is Wisp?", "selected", {"wisp-arch-020"},
            "definitional, but Wisp = project identifier"),
    LLMCase("What is the GenerationCounter?",
            "selected", {"pyside-qthread-012"}, "project identifier def"),

    # ============ Pure off-domain repeats (capacity) ======================
    LLMCase("Best pizza in Brooklyn?", "none", set(), "off-domain"),
    LLMCase("How do I tie a tie?", "none", set(), "off-domain"),
    LLMCase("What's a good push-up form?", "none", set(), "off-domain"),
    LLMCase("Translate 'hello' to Spanish.", "none", set(), "off-domain"),

    # ============ Borderline: generic CS but might pull a chunk ===========
    LLMCase("What is the difference between fetch and pull, in any VCS?",
            "tiny", set(), "generic VCS framing — should NOT pull project chunk"),
    LLMCase("Explain branches in any DVCS.",
            "tiny", set(), "generic"),
]


# --- comparison helpers ----------------------------------------------------

def _level_agreement(router_level: str, llm_level: str) -> str:
    """exact | adjacent | far — how far apart on the ladder."""
    if router_level == llm_level:
        return "exact"
    d = abs(LEVEL_RANK[router_level] - LEVEL_RANK[llm_level])
    return "adjacent" if d == 1 else "far"


def _chunk_metrics(router_sel: list[str], llm_sel: set[str]) -> dict:
    rs = set(router_sel)
    if not llm_sel and not rs:
        return {"jaccard": 1.0, "p": 1.0, "r": 1.0, "f1": 1.0,
                "missed": set(), "extra": set()}
    if not llm_sel:
        return {"jaccard": 0.0, "p": 0.0, "r": 1.0, "f1": 0.0,
                "missed": set(), "extra": rs}
    if not rs:
        return {"jaccard": 0.0, "p": 1.0, "r": 0.0, "f1": 0.0,
                "missed": set(llm_sel), "extra": set()}
    tp = len(rs & llm_sel)
    p = tp / len(rs)
    r = tp / len(llm_sel)
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    union = len(rs | llm_sel)
    return {
        "jaccard": tp / union if union else 1.0,
        "p": p, "r": r, "f1": f1,
        "missed": llm_sel - rs,
        "extra": rs - llm_sel,
    }


def _classify_disagreement(router_level: str, llm_level: str, m: dict) -> str:
    """Bucket each disagreement so tuning signals group cleanly."""
    if router_level == llm_level and not m["missed"] and not m["extra"]:
        return "agree"
    rr, lr = LEVEL_RANK[router_level], LEVEL_RANK[llm_level]
    if rr > lr and not llm_sel_empty(m, llm_level):
        return "router_over_levels"
    if rr > lr:
        return "router_over_should_be_none_or_tiny"
    if rr < lr:
        return "router_under"
    # same level, different chunks
    if m["missed"] and not m["extra"]:
        return "missed_chunks"
    if m["extra"] and not m["missed"]:
        return "extra_chunks"
    return "swapped_chunks"


def llm_sel_empty(m: dict, llm_level: str) -> bool:
    return llm_level in ("none", "tiny")


# --- main eval -------------------------------------------------------------

def _route_fn(router: ContextRouter, use_ladder: bool):
    return router.route_ladder if use_ladder else router.route


def evaluate(use_ladder: bool):
    router = ContextRouter()
    route = _route_fn(router, use_ladder)
    rows = []
    for c in CASES:
        res = route(c.prompt)
        lvl_agree = _level_agreement(res.context_level, c.level)
        m = _chunk_metrics(res.selected_chunk_ids, c.chunks)
        bucket = _classify_disagreement(res.context_level, c.level, m)
        rows.append((c, res, lvl_agree, m, bucket))
    return router, rows


def _print_rows(rows, show_only_fails: bool) -> None:
    for c, res, lvl_agree, m, bucket in rows:
        if show_only_fails and bucket == "agree":
            continue
        tag = "OK " if bucket == "agree" else "XX "
        print(f"{tag} lvl[{lvl_agree:>8}]  J={m['jaccard']:.2f}  {c.prompt}")
        if bucket != "agree":
            print(f"     router : {res.context_level:<9} {sorted(res.selected_chunk_ids)}")
            print(f"     llm    : {c.level:<9} {sorted(c.chunks)}  ({c.rationale})")
            print(f"     bucket : {bucket}   reason: {res.reason}")
            if m["missed"]:
                print(f"     missed : {sorted(m['missed'])}")
            if m["extra"]:
                print(f"     extra  : {sorted(m['extra'])}")


def _print_aggregates(rows, label: str) -> None:
    n = len(rows)
    exact = sum(1 for r in rows if r[2] == "exact")
    adjacent = sum(1 for r in rows if r[2] == "adjacent")
    far = sum(1 for r in rows if r[2] == "far")
    agree = sum(1 for r in rows if r[4] == "agree")
    avg_j = sum(r[3]["jaccard"] for r in rows) / n
    avg_p = sum(r[3]["p"] for r in rows) / n
    avg_r = sum(r[3]["r"] for r in rows) / n
    avg_f = sum(r[3]["f1"] for r in rows) / n
    print()
    print(f"=== {label} ===")
    print(f"cases:              {n}")
    print(f"level exact match:  {exact}/{n}  ({exact/n:.0%})")
    print(f"level adjacent:     {adjacent}/{n}  ({adjacent/n:.0%})")
    print(f"level FAR (>=2):    {far}/{n}  ({far/n:.0%})")
    print(f"full agree (lvl+ch):{agree}/{n}  ({agree/n:.0%})")
    print(f"avg Jaccard chunks: {avg_j:.2f}")
    print(f"avg P/R/F1 chunks:  {avg_p:.2f} / {avg_r:.2f} / {avg_f:.2f}")


def _print_confusion(rows) -> None:
    print()
    print("Level confusion matrix (rows=LLM, cols=router):")
    mat = {l: {r: 0 for r in LEVELS} for l in LEVELS}
    for c, res, _la, _m, _b in rows:
        mat[c.level][res.context_level] += 1
    header = "          " + "".join(f"{r:>10}" for r in LEVELS)
    print(header)
    for l in LEVELS:
        row = f"{l:>10}" + "".join(f"{mat[l][r]:>10}" for r in LEVELS)
        print(row)


def _print_buckets(rows) -> None:
    print()
    print("Disagreement buckets (training signals):")
    buckets: dict[str, int] = {}
    for r in rows:
        buckets[r[4]] = buckets.get(r[4], 0) + 1
    for k in sorted(buckets, key=lambda x: -buckets[x]):
        print(f"  {k:<35} {buckets[k]:>3}")


def run(show_only_fails: bool, use_ladder: bool, confusion: bool) -> int:
    label = "ladder (legacy)" if use_ladder else "additive (new)"
    router, rows = evaluate(use_ladder)
    print(f"Embedder: {router.embedder.name} | chunks: {len(router.chunks)} "
          f"| pipeline: {label} | cases: {len(CASES)}\n")
    print(f"{'tag':>3} {'level':>14}  {'J':>4}  prompt")
    print("-" * 100)
    _print_rows(rows, show_only_fails)
    print("-" * 100)
    _print_aggregates(rows, label)
    _print_buckets(rows)
    if confusion:
        _print_confusion(rows)
    fails = sum(1 for r in rows if r[4] != "agree")
    return 0 if fails == 0 else 1


def run_compare() -> int:
    router, ladder_rows = evaluate(use_ladder=True)
    _, add_rows = evaluate(use_ladder=False)
    print(f"Embedder: {router.embedder.name} | chunks: {len(router.chunks)} "
          f"| LLM-oracle compare | cases: {len(CASES)}\n")
    flipped_better = flipped_worse = unchanged_bad = unchanged_good = 0
    for (cl, lr, _la, _lm, lb), (_, ar, _aa, _am, ab) in zip(ladder_rows, add_rows):
        if lb != "agree" and ab == "agree":
            flipped_better += 1
        elif lb == "agree" and ab != "agree":
            flipped_worse += 1
            print(f"  REGRESS  {cl.prompt}")
            print(f"    ladder : {lr.context_level} {sorted(lr.selected_chunk_ids)}")
            print(f"    addit. : {ar.context_level} {sorted(ar.selected_chunk_ids)}")
            print(f"    llm    : {cl.level} {sorted(cl.chunks)}")
        elif lb != "agree" and ab != "agree":
            unchanged_bad += 1
        else:
            unchanged_good += 1
    print()
    print(f"additive FIXED vs ladder: {flipped_better}")
    print(f"additive REGRESSED:       {flipped_worse}")
    print(f"both still wrong:         {unchanged_bad}")
    print(f"both already right:       {unchanged_good}")
    _print_aggregates(ladder_rows, "ladder")
    _print_aggregates(add_rows, "additive")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval_vs_llm", description=__doc__)
    ap.add_argument("--fails", action="store_true",
                    help="show only cases where router disagrees with the LLM oracle")
    ap.add_argument("--ladder", action="store_true",
                    help="evaluate the legacy ladder router instead of additive")
    ap.add_argument("--confusion", action="store_true",
                    help="print level confusion matrix")
    ap.add_argument("--compare", action="store_true",
                    help="run both pipelines side by side")
    args = ap.parse_args(argv)
    if args.compare:
        return run_compare()
    return run(args.fails, args.ladder, args.confusion)


if __name__ == "__main__":
    sys.exit(main())
