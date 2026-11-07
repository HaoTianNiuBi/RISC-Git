"""Relative energy score model (docs/03 §5.6).

score = Σ(类别动态计数 × 权重).  The weights are illustrative constants, not a
hardware power model: the score is only meaningful as a before/after trend on
the same benchmark (docs/06 §2 要求所有展示处附免责声明).

Weights may be overridden from config.toml (wired by pipeline via run_meta
"weights"); any override snapshot is stored in the metrics report.
"""
from __future__ import annotations

DEFAULT_WEIGHTS: dict[str, float] = {
    "integer": 1.0,
    "memory": 3.0,
    "branch": 1.5,
    "jump": 1.0,
    "muldiv": 4.0,
    "float": 8.0,
    "system": 1.0,
}

_DISCLAIMER = "相对能耗估算，仅用于优化前后趋势对比，不替代真实硬件功耗测试"


def energy_score(metrics: dict, weights: dict[str, float] | None = None) -> dict:
    """Estimate the relative energy score from a MetricsReport-like dict.

    Reads `metrics["dynamic"]["by_class"]`; classes absent from the report
    contribute 0. Unknown classes fall back to the integer weight (1.0) so a
    mapping-table drift can never silently drop executed instructions from
    the score. Returns the score plus the exact weights used (audit snapshot).
    """
    w = dict(DEFAULT_WEIGHTS if weights is None else weights)
    by_class = (metrics.get("dynamic") or {}).get("by_class") or {}
    score = 0.0
    for cls, count in by_class.items():
        score += float(count) * float(w.get(cls, w.get("integer", 1.0)))
    return {
        "score": int(round(score)),
        "weights_used": {k: float(v) for k, v in w.items()},
        "disclaimer": _DISCLAIMER,
    }
