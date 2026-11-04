"""Suggesting a threshold from review answers — one way for every sensor kind: each answer is a
score and whether it was right ("yes") or not ("no"); the threshold that keeps the most "yes" and
the fewest "no" is suggested, and the user decides (docs/SCOPE.md)."""

from __future__ import annotations

from .settings import ADVICE


def threshold_advice(yes: list[float], no: list[float], current: float, limits: tuple[float, float]) -> dict:
    """A threshold from the answers to review questions: the score of each thing asked about (a
    sound's best score, a state's confidence…), answered "yes" (right) or "no" (wrong). It is the
    one that lets the most "yes" and the fewest "no" pass (score at least the threshold), a "yes"
    held back counting twice. ``suggested`` is None without ADVICE["min_answers"] answers or a
    better threshold than ``current``; ``separable`` says whether one threshold lets every "yes"
    and no "no" pass."""

    def counts(t: float) -> dict:
        return {
            "threshold": round(t, 2),
            "yes_passed": sum(s >= t for s in yes),
            "no_passed": sum(s >= t for s in no),
        }

    def cost(c: dict) -> int:
        return 2 * (len(yes) - c["yes_passed"]) + c["no_passed"]

    now = counts(current)
    out = {"yes": len(yes), "no": len(no), "current": now, "suggested": None, "separable": False}
    if len(yes) + len(no) < ADVICE["min_answers"]:
        return out
    lo, hi = limits
    candidates = [counts(lo + i / 100) for i in range(round((hi - lo) * 100) + 1)]
    out["separable"] = any(c["yes_passed"] == len(yes) and not c["no_passed"] for c in candidates)
    best = min(candidates, key=lambda c: (cost(c), abs(c["threshold"] - current)))
    if cost(best) < cost(now) and abs(best["threshold"] - current) >= 0.02:
        out["suggested"] = best
    return out
