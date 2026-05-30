"""
router.py — combine signals, score chunks, choose a context level.

Pipeline (mirrors the plan's "Main Pipeline"):
    query
      -> extract terms / phrases / identifiers
      -> exact identifier + phrase lookup (inverted index)
      -> rarity (IDF) scoring of matched terms
      -> vector similarity (pluggable embedder)
      -> per-chunk combined score
      -> pick context level (none | tiny | selected | full)

Scoring formula (per chunk), from the plan, weights tunable below:
    score = 0.40*vec + 0.30*rareTerm + 0.25*phraseOrIdent
            + 0.10*recency - 0.15*commonTermPenalty

The routing *decision* then layers rules on top of the raw scores: a single
rare identifier match (e.g. ``origin/main``) is enough for "selected" even if
vector similarity is mediocre, while a generic definition question is pushed
toward "none"/"tiny" regardless of incidental overlap.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .chunks import ContextChunk, load_seed_chunks
from .embeddings import make_embedder
from .extract import extract, Extracted
from .index import RetrievalIndexes, build_indexes

log = logging.getLogger("context_router")


# --- tunable weights --------------------------------------------------------
W_VEC = 0.40
W_RARE_TERM = 0.30
W_PHRASE_IDENT = 0.25
W_RECENCY = 0.10
W_COMMON_PENALTY = 0.15

# Level thresholds on the top chunk's combined score.
T_SELECTED = 0.30     # at/above -> at least "selected"
T_TINY = 0.12         # between T_TINY and T_SELECTED -> weak; tiny unless rule fires
RECENCY_HALFLIFE_DAYS = 14.0

# Per-match-type inclusion floors. A chunk is only added to the selected set if
# it clears the floor for the *match type that won the route*. Different match
# types live on different score scales (an identifier match tops out ~0.7, a
# semantic-only match ~0.32), so a single global floor can't separate noise in
# a strong query from signal in a weak one. These are starting values — tune
# them from an eval set, not by eye.
FLOORS: dict[str, float] = {
    "identifier": 0.18,   # identifier anchors topic strongly; keep on-topic secondaries
    "phrase":     0.18,
    "semantic":   0.20,   # dense vecs give everything a small score; bar must be higher
    "uncertain":  T_TINY,  # weak middle band; keep only the very top 1-2
}

# Relative cutoff: a chunk must also score at least this fraction of the TOP
# chunk's score to be kept. This adapts per query — when one match dominates
# (e.g. an identifier hit at 0.97) it trims the long tail of coincidental
# matches that a fixed floor lets through. Effective bar = max(floor, REL*top).
# Value chosen by the --sweep eval, not by eye: 0.55 maximises F1 (P=0.95,
# R=0.88) on the current eval set. Recall is flat from 0.40-0.60, so 0.55 is
# free precision in that band. Drop to ~0.35 if you'd rather protect recall
# (R=0.92, P=0.80). 0.0 disables the relative cutoff entirely.
REL_CUTOFF = 0.55

# --- additive pipeline tunables --------------------------------------------
# These drive the new route(). The ladder constants above are kept only for
# route_ladder() (A/B baseline).
#
# Design note: the per-chunk score formula (_score_chunk) ALREADY weights
# phrase/identifier (0.25), rare-term (0.30), and recency (0.10). Adding more
# bonuses for those here would double-count and saturate confidence on strong
# matches. The additive layer here only contributes signals the base score
# doesn't see: breadth, definitional phrasing, follow-up, vec-only-ness.

# Absolute admission floor: if the top chunk scores below this AND there's no
# identifier rescue, we retrieve nothing regardless of bonus accumulation.
T_ABS = 0.15

# Positive evidence (added to confidence) -- breadth only; the rest is in base.
W_BREADTH   = 0.08   # per extra chunk in top-5 above breadth bar
BREADTH_BAR = 0.25   # what counts as "another relevant chunk" for breadth

# Negative evidence (subtracted from confidence) -- query-shape signals that
# the base score can't see.
W_DEF_PENALTY      = 0.08   # query looks like a generic "what is X" question
W_FOLLOWUP_PENALTY = 0.15   # short anaphoric follow-up
W_VECONLY_PENALTY  = 0.20   # top match is pure embedding vibes, no real evidence

# "Real evidence" gate used by vec_only and the safety rail: top chunk has
# at least one phrase/identifier match, OR strong rare-term overlap.
RARETERM_EVIDENCE_BAR = 0.50

# Level thresholds on confidence. Anchored to base-score distribution from the
# eval set: strong matches sit 0.7-1.1, medium 0.3-0.5, weak <0.15. Confidence
# is base ± small adjustments, so thresholds live in the same range.
L_FULL     = 0.85
L_SELECTED = 0.35
L_TINY     = 0.20

# Minimum breadth required to upgrade to `full`. A single strong chunk + one
# tangential neighbour shouldn't reach full; that's a `selected` query.
FULL_BREADTH_MIN = 3

# Step-8 chunk-picking knobs once the level is decided.
REL_CUTOFF_SELECTED = 0.55   # selected: stricter, precision-oriented
REL_CUTOFF_FULL     = 0.55   # full: keep precision; breadth comes from limit, not cutoff
ABS_INCLUDE_FLOOR   = 0.25   # never include a chunk scoring below this absolute bar

# Definition / generic question patterns -> bias toward none/tiny.
_DEFINITION_RE = re.compile(
    r"^\s*(what\s+(is|are|does|do)\b|what'?s\b|define\b|meaning\s+of\b|"
    r"explain\s+(what|the\s+(term|word|concept))\b|"
    r"\w+\s+vs\.?\s+\w+\s*\??$)",
    re.IGNORECASE,
)
# Generic-framing markers: even a query that mentions a project term should be
# treated as a definition if the user explicitly disclaims their own context
# ("in general", "in any X", "conceptually", "generally, not for my repo").
_GENERIC_FRAMING_RE = re.compile(
    r"(?:\bin general\b|\bgenerally\b(?=[,\s])|\bconceptually\b|"
    r"\bin any\s+\w+|\bas a concept\b|\bthe concept of\b|\bin theory\b)",
    re.IGNORECASE,
)
# Short anaphoric follow-ups ("explain that simpler", "why is it slow?").
_FOLLOWUP_RE = re.compile(
    r"\b(that|this|it|these|those|again|simpler|the same)\b", re.IGNORECASE
)


@dataclass
class ChunkScore:
    chunk_id: str
    score: float
    vec: float
    rare_term: float
    phrase_ident: float
    recency: float
    common_penalty: float
    matched_identifiers: list[str] = field(default_factory=list)
    matched_phrases: list[str] = field(default_factory=list)
    matched_rare_terms: list[str] = field(default_factory=list)


@dataclass
class RouteResult:
    context_level: str            # none | tiny | selected | full
    selected_chunk_ids: list[str]
    confidence: float
    reason: str
    scores: list[ChunkScore] = field(default_factory=list)  # full debug, top-down
    match_type: str = ""          # which signal won the route (identifier/phrase/semantic/...)
    applied_floor: float = 0.0    # inclusion floor used for chunk selection
    dropped_chunk_ids: list[str] = field(default_factory=list)  # within limit but below floor

    def to_dict(self) -> dict:
        return {
            "context_level": self.context_level,
            "selected_chunk_ids": self.selected_chunk_ids,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
        }


class ContextRouter:
    def __init__(
        self, chunks: list[ContextChunk] | None = None, *, rel_cutoff: float = REL_CUTOFF
    ) -> None:
        self.chunks: list[ContextChunk] = chunks if chunks is not None else load_seed_chunks()
        self.by_id: dict[str, ContextChunk] = {c.id: c for c in self.chunks}
        self.index: RetrievalIndexes = build_indexes(self.chunks)
        self.embedder = make_embedder(self.chunks)
        # Mutable so an eval sweep can try several values without reloading the model.
        self.rel_cutoff: float = rel_cutoff

    def _eff_floor(self, base_floor: float, top: "ChunkScore | None") -> float:
        """Floor lifted to the relative cutoff: max(base, REL * top_score)."""
        if top is None or self.rel_cutoff <= 0:
            return base_floor
        return max(base_floor, self.rel_cutoff * top.score)

    # ------------------------------------------------------------------
    # per-chunk scoring
    # ------------------------------------------------------------------
    def _recency(self, chunk: ContextChunk, now: float) -> float:
        import time
        age_days = max(0.0, (now - chunk.last_used_at) / 86400.0)
        return 0.5 ** (age_days / RECENCY_HALFLIFE_DAYS)  # 1.0 fresh -> 0 old

    def _score_chunk(self, chunk: ContextChunk, q: Extracted, query: str, now: float) -> ChunkScore:
        idx = self.index
        c_terms = {t.lower() for t in chunk.terms}
        c_phrases = {p.lower() for p in chunk.phrases}
        c_idents = {i.lower() for i in chunk.identifiers}

        # 1+2. exact identifier / phrase matches (strongest distinctive signal)
        matched_idents = [i for i in q.identifiers if i.lower() in c_idents]
        matched_phrases = [p for p in q.phrases if p.lower() in c_phrases]
        ident_score = sum(idx.identifier_idf(i) for i in matched_idents)
        phrase_score = sum(idx.phrase_idf(p) for p in matched_phrases)
        # Normalise into ~0..1 by a soft cap.
        phrase_ident = min(1.0, (ident_score + phrase_score) / 3.0)

        # 3. rare-term overlap, weighted by IDF, common terms contribute little
        matched_terms = [t for t in q.terms if t.lower() in c_terms]
        max_idf = idx.max_term_idf() or 1.0
        rare_sum = 0.0
        rare_hits: list[str] = []
        for t in matched_terms:
            if idx.is_common(t):
                continue
            w = idx.term_idf(t) / max_idf
            rare_sum += w
            if w > 0.35:
                rare_hits.append(t)
        rare_term = min(1.0, rare_sum / 2.0)

        # 4. semantic similarity
        vec = max(0.0, min(1.0, self.embedder.similarity(query, chunk.text)))

        # 5. recency
        recency = self._recency(chunk, now)

        # 6. common-word penalty: fraction of matched terms that are common
        if matched_terms:
            common_frac = sum(1 for t in matched_terms if idx.is_common(t)) / len(matched_terms)
        else:
            common_frac = 0.0
        # Only penalise when commonality is the *main* thing matching.
        common_penalty = common_frac if (rare_term < 0.1 and phrase_ident < 0.1) else 0.0

        score = (
            W_VEC * vec
            + W_RARE_TERM * rare_term
            + W_PHRASE_IDENT * phrase_ident
            + W_RECENCY * recency
            - W_COMMON_PENALTY * common_penalty
        )
        # importance acts as a mild prior multiplier on the evidence terms
        score *= 0.75 + 0.5 * chunk.importance

        return ChunkScore(
            chunk_id=chunk.id,
            score=round(score, 4),
            vec=round(vec, 4),
            rare_term=round(rare_term, 4),
            phrase_ident=round(phrase_ident, 4),
            recency=round(recency, 4),
            common_penalty=round(common_penalty, 4),
            matched_identifiers=matched_idents,
            matched_phrases=matched_phrases,
            matched_rare_terms=rare_hits,
        )

    # ------------------------------------------------------------------
    # routing decision — additive pipeline (8 steps)
    # ------------------------------------------------------------------
    def route(self, query: str) -> RouteResult:
        """Score-then-budget pipeline. See module docstring for the 8 steps.

        1+2. Score all chunks; take the top one.
        3.   Start a confidence number at top.score.
        4.   Add bonuses for evidence the user wants stored context
             (identifier hit, phrase hit, rare-term overlap, breadth, recency).
        5.   Subtract penalties for evidence they don't
             (definitional phrasing, anaphoric follow-up, vec-only noise).
        6.   Safety rails: absolute floor (force none), identifier override
             (never drop below selected when a rare id matched).
        7.   Single threshold table -> level.
        8.   Pick chunks to fit the chosen level's budget.
        """
        import time
        now = time.time()
        q = extract(query)

        # --- 1 + 2: score and pick the top chunk -----------------------
        scores = sorted(
            (self._score_chunk(c, q, query, now) for c in self.chunks),
            key=lambda s: s.score,
            reverse=True,
        )
        top = scores[0] if scores else None
        if top is None:
            return self._mk(query, "none", [], 0.5, "No chunks indexed.",
                            scores, match_type="empty")

        # --- detect signals --------------------------------------------
        has_ident = bool(top.matched_identifiers)
        has_phrase = bool(top.matched_phrases)
        strong_rareterm = top.rare_term >= RARETERM_EVIDENCE_BAR
        # "vec-only" = top match has no exact phrase/id and only weak rare-term
        # overlap. Pure embedding similarity; the joke/markup failure mode.
        vec_only = (top.phrase_ident < 0.05) and (not strong_rareterm) and (not has_ident)

        is_definition = bool(_DEFINITION_RE.search(query.strip()))
        # Generic-framing ("in general", "in any VCS", "conceptually") counts as
        # a definitional question even without a "what is" prefix — the user is
        # explicitly disclaiming their own stored context.
        is_generic_framing = bool(_GENERIC_FRAMING_RE.search(query))
        if is_generic_framing:
            is_definition = True
        is_followup = bool(_FOLLOWUP_RE.search(query)) and len(q.terms) <= 4

        # breadth signal: how many of the top chunks look genuinely relevant?
        breadth = sum(1 for s in scores[:5] if s.score >= BREADTH_BAR)

        # --- 3: start confidence at the top score ----------------------
        base = top.score

        # --- 4: positive evidence --------------------------------------
        # Only breadth here -- identifier/phrase/rare-term/recency are already
        # in the base score (see _score_chunk weights). Adding them again
        # would double-count and saturate confidence.
        bonuses = 0.0
        if breadth > 1:
            bonuses += W_BREADTH * (breadth - 1)

        # --- 5: negative evidence --------------------------------------
        penalties = 0.0
        if is_definition:
            penalties += W_DEF_PENALTY
        if is_followup:
            penalties += W_FOLLOWUP_PENALTY
        if vec_only:
            penalties += W_VECONLY_PENALTY

        conf = base + bonuses - penalties

        # --- 6: safety rails -------------------------------------------
        # Absolute floor: top chunk is too weak and we have no identifier to
        # rescue it -> retrieve nothing, no matter what bonuses accumulated.
        if top.score < T_ABS and not has_ident:
            return self._mk(
                query, "none", [], 0.6,
                f"Top score {top.score:.2f} below T_ABS={T_ABS}; "
                f"no identifier rescue. (base={base:.2f} +{bonuses:.2f} "
                f"-{penalties:.2f} = conf {conf:.2f}, ignored)",
                scores, match_type="below_abs_floor",
            )
        # Identifier override: a rare identifier hit guarantees at least
        # `selected` — a 'what is TaskJar?' query shouldn't be killed by the
        # definition penalty.
        rail_notes = []
        if has_ident:
            forced = L_SELECTED + 0.02
            if conf < forced:
                rail_notes.append("identifier override")
                conf = forced
        # Rare-term rescue: same idea, for distinctive single-word project
        # terms that don't match the identifier regex (e.g. 'Wisp', 'TaskJar'
        # without the camel-case). If rare-term evidence is strong, don't let
        # the definition penalty drop us below `selected`.
        elif strong_rareterm:
            forced = L_SELECTED + 0.02
            if conf < forced:
                rail_notes.append("rare-term override")
                conf = forced

        conf = max(0.0, min(1.2, conf))

        # --- 7: threshold table -> level -------------------------------
        # `full` requires both high confidence AND breadth — a single strong
        # chunk should sit at `selected` and not drag a secondary in.
        if conf >= L_FULL and breadth >= FULL_BREADTH_MIN:
            level = "full"
        elif conf >= L_SELECTED:
            level = "selected"
        elif conf >= L_TINY:
            level = "tiny"
        else:
            level = "none"
        # Generic-framing cap: if the user explicitly disclaimed their own
        # stored context ("in general", "in any VCS", "conceptually") and no
        # rare identifier rescued the route, cap at `tiny`. Selected/full are
        # for queries the user wants answered with *their* memory.
        if is_generic_framing and not has_ident and level in ("selected", "full"):
            level = "tiny"
            rail_notes.append("generic-framing cap")

        # --- 8: pick chunks to fit the level's budget ------------------
        # Inclusion bar = max(relative cutoff, absolute include floor). The
        # absolute floor stops near-zero secondaries from sneaking in when the
        # top chunk is weak.
        sel: list[str] = []
        dropped: list[str] = []
        if level == "tiny":
            sel = [top.chunk_id]
        elif level == "selected":
            bar = max(REL_CUTOFF_SELECTED * top.score, ABS_INCLUDE_FLOOR)
            for s in scores[:5]:
                (sel if s.score >= bar else dropped).append(s.chunk_id)
        elif level == "full":
            bar = max(REL_CUTOFF_FULL * top.score, ABS_INCLUDE_FLOOR)
            for s in scores[:7]:
                (sel if s.score >= bar else dropped).append(s.chunk_id)
            # Post-hoc safety: `full` claims breadth, but the inclusion bar
            # uses a stricter relative cutoff than BREADTH_BAR, so a query
            # with one dominant chunk + tangential neighbours can pass
            # FULL_BREADTH_MIN at scoring time and still end up with only one
            # includable chunk. If that happens, demote to `selected` so the
            # level label matches what we actually return.
            if len(sel) < FULL_BREADTH_MIN:
                level = "selected"
                rail_notes.append(f"full -> selected (only {len(sel)} chunk(s) cleared floor)")

        # diagnostic match_type: what carried the decision?
        if has_ident:
            match_type = "identifier"
        elif has_phrase:
            match_type = "phrase"
        elif strong_rareterm:
            match_type = "rare_term"
        elif vec_only:
            match_type = "vec_only"
        else:
            match_type = "additive"

        reason = (
            f"conf={conf:.2f} (base {base:.2f} +{bonuses:.2f} -{penalties:.2f})"
            f" -> {level}"
        )
        if rail_notes:
            reason += "; " + ", ".join(rail_notes)

        return self._mk(
            query, level, sel, min(0.95, conf), reason,
            scores, match_type=match_type, floor=L_TINY, dropped=dropped,
        )

    # ------------------------------------------------------------------
    # legacy ladder routing (kept for A/B against the additive pipeline)
    # ------------------------------------------------------------------
    def route_ladder(self, query: str) -> RouteResult:
        """Original first-match-wins rule ladder. Kept so eval_large can
        diff it against the new additive route()."""
        import time
        now = time.time()
        q = extract(query)

        scores = sorted(
            (self._score_chunk(c, q, query, now) for c in self.chunks),
            key=lambda s: s.score,
            reverse=True,
        )
        top = scores[0] if scores else None

        is_definition = bool(_DEFINITION_RE.search(query.strip()))
        is_followup = bool(_FOLLOWUP_RE.search(query)) and len(q.terms) <= 4
        has_rare_ident = bool(top and top.matched_identifiers)
        has_rare_phrase = bool(top and top.matched_phrases)

        log.debug(
            "route start q=%r terms=%s idents=%s phrases=%s | top=%s score=%.3f | "
            "definition=%s followup=%s",
            query, q.terms, q.identifiers, q.phrases,
            top.chunk_id if top else None, top.score if top else 0.0,
            is_definition, is_followup,
        )

        # Rule order matters — most decisive signals first.

        # Generic definition question: only a truly distinctive *identifier*
        # (e.g. "TaskJar") may escalate it. A definitional phrase matching a
        # generic chunk ("lifecycle hook") must NOT pull project context.
        if is_definition and not has_rare_ident:
            level = "tiny" if (top and top.score >= T_TINY) else "none"
            return self._mk(
                query, level, [], 0.6,
                "Generic/definition question; no distinctive project identifier.",
                scores, match_type="definition",
            )

        # Strong distinctive match -> selected, even on mediocre vec similarity.
        if has_rare_ident or has_rare_phrase:
            match_type = "identifier" if has_rare_ident else "phrase"
            floor = self._eff_floor(FLOORS[match_type], top)
            sel, dropped = self._collect(scores, limit=5, floor=floor, match_type=match_type)
            hit = (top.matched_identifiers or top.matched_phrases)[0]
            level = "full" if (top.score >= T_SELECTED and len(sel) >= 4) else "selected"
            conf = min(0.95, 0.7 + 0.25 * min(1.0, top.score / T_SELECTED))
            return self._mk(
                query, level, sel, conf, f"Matched rare {match_type} '{hit}'.",
                scores, match_type=match_type, floor=floor, dropped=dropped,
            )

        # Short anaphoric follow-up -> tiny (lean on conversation, not memory).
        if is_followup and (top is None or top.score < T_SELECTED):
            return self._mk(
                query, "tiny", [], 0.55,
                "Short follow-up; relies on recent turns, not stored context.",
                scores, match_type="followup",
            )

        # Strong semantic/rare-term score without an exact id -> selected (medium conf).
        if top and top.score >= T_SELECTED:
            floor = self._eff_floor(FLOORS["semantic"], top)
            sel, dropped = self._collect(scores, limit=5, floor=floor, match_type="semantic")
            return self._mk(
                query, "selected", sel, min(0.8, 0.5 + top.score),
                "Strong relevance but no exact identifier; selected, kept small.",
                scores, match_type="semantic", floor=floor, dropped=dropped,
            )

        # Uncertain middle band -> selected but minimal (top 1-2), low confidence.
        if top and top.score >= T_TINY:
            floor = FLOORS["uncertain"]
            sel, dropped = self._collect(scores, limit=2, floor=floor, match_type="uncertain")
            return self._mk(
                query, "selected", sel, 0.4,
                "Uncertain match; using a small amount of context.",
                scores, match_type="uncertain", floor=floor, dropped=dropped,
            )

        # Nothing distinctive matched.
        return self._mk(
            query, "none", [], 0.5,
            "No relevant or distinctive context found.", scores, match_type="none",
        )

    def _collect(
        self, scores: list[ChunkScore], *, limit: int, floor: float, match_type: str
    ) -> tuple[list[str], list[str]]:
        """Split the top *limit* chunks by the floor; return (kept, dropped).

        ``dropped`` are chunks that were in contention (within the limit) but
        fell below the floor — the useful debug signal for tuning the floor.
        """
        kept: list[str] = []
        dropped: list[str] = []
        for s in scores[:limit]:
            (kept if s.score >= floor else dropped).append(s.chunk_id)
        if dropped:
            log.debug(
                "collect[%s] floor=%.3f kept=%s dropped_below_floor=%s",
                match_type, floor, kept,
                [(cid, round(s.score, 3)) for s in scores[:limit]
                 for cid in [s.chunk_id] if s.score < floor],
            )
        return kept, dropped

    def _mk(
        self, query: str, level: str, sel: list[str], conf: float, reason: str,
        scores: list[ChunkScore], *, match_type: str = "", floor: float = 0.0,
        dropped: list[str] | None = None,
    ) -> RouteResult:
        res = RouteResult(
            level, sel, conf, reason, scores,
            match_type=match_type, applied_floor=floor, dropped_chunk_ids=dropped or [],
        )
        log.info(
            "route q=%r -> level=%s conf=%.2f match=%s floor=%.3f sel=%s dropped=%s",
            query, level, conf, match_type, floor, sel, res.dropped_chunk_ids,
        )
        return res

    # convenience
    def explain(self, query: str, top_n: int = 5) -> str:
        res = self.route(query)
        lines = [
            f"Q: {query}",
            f"-> level={res.context_level}  conf={res.confidence:.2f}  "
            f"match={res.match_type}  floor={res.applied_floor:.2f}",
            f"   reason: {res.reason}",
            f"   selected: {res.selected_chunk_ids or '[]'}",
            f"   dropped (below floor): {res.dropped_chunk_ids or '[]'}",
            "   top chunks:",
        ]
        for s in res.scores[:top_n]:
            bits = []
            if s.matched_identifiers:
                bits.append(f"id={s.matched_identifiers}")
            if s.matched_phrases:
                bits.append(f"ph={s.matched_phrases}")
            if s.matched_rare_terms:
                bits.append(f"rare={s.matched_rare_terms}")
            lines.append(
                f"     {s.score:+.3f} {s.chunk_id:<24} "
                f"vec={s.vec:.2f} rare={s.rare_term:.2f} "
                f"pi={s.phrase_ident:.2f} rec={s.recency:.2f} "
                f"{' '.join(bits)}"
            )
        return "\n".join(lines)
