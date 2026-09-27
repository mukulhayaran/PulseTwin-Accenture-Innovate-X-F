"""Alarm thresholds -- the two that disagree.

ADR-004. The original spec asked for one "cost-optimal threshold" that also respects a
6-alerts-per-shift budget. Those are not the same number and cannot be reconciled:

    cost-optimal      p* = C_alarm / (C_miss + C_alarm) = 45 / 7045 = 0.0064
    budget-constrained  whatever threshold admits <= 6 units per shift

A miss costs ~155x what a false alarm costs, so pure economics says alarm on nearly
everything. Human factors research caps a supervisor at 6-8 actionable alerts per shift
(Wickens, 2008). Both are computed and both are published; the system operates on the
budget one and quantifies what that costs.

That is a better answer to "false alarms erode trust" than a single reconciled number,
because it names the real binding constraint: human attention, not model quality.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from offline.config_schema import EconomicsConfig


@dataclass
class ThresholdReport:
    cost_optimal: float
    budget_constrained: float
    alerts_per_shift_at_cost_optimal: float
    alerts_per_shift_at_budget: float
    precision_at_budget: float
    recall_at_budget: float
    recall_at_cost_optimal: float
    theoretical_gap_per_year: float      # vs the INFEASIBLE cost-optimal point
    doubled_budget_threshold: float
    doubled_budget_recall: float
    marginal_value_of_doubling: float    # the decision a manager can actually make
    marginal_cost_of_doubling: float
    units_per_shift: float
    curve: list[dict] = field(default_factory=list)
    # ADR-061: where the budget threshold was CHOSEN, and what its ties did. Before this,
    # train.py chose it on the test scores it is then measured on.
    threshold_selected_on: str = "test"
    budget_alerts_intended: int = 0
    budget_alerts_at_selection: int = 0
    score_count_at_budget_threshold: int = 0

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "curve"}
        d["alarm_budget_curve"] = self.curve
        return d


def cost_of_miss(econ: EconomicsConfig, escape_rate: float) -> float:
    """Expected cost of letting one defective unit through."""
    return (
        econ.cost("scrap_cost_per_unit")
        + econ.cost("rework_cost_per_unit")
        + econ.cost("warranty_escape_cost_per_unit") * escape_rate
    )


def cost_optimal_threshold(econ: EconomicsConfig, escape_rate: float) -> float:
    c_alarm = econ.cost("supervisor_response_cost_per_alarm")
    return c_alarm / (cost_of_miss(econ, escape_rate) + c_alarm)


def _rates(y: np.ndarray, p: np.ndarray, thr: float) -> tuple[float, float, int]:
    flagged = p >= thr
    n_flag = int(flagged.sum())
    if n_flag == 0:
        return 0.0, 0.0, 0
    precision = float(y[flagged].mean())
    recall = float(y[flagged].sum() / max(1, y.sum()))
    return precision, recall, n_flag


def build_thresholds(
    y: np.ndarray,
    p: np.ndarray,
    econ: EconomicsConfig,
    units_per_shift: float,
    escape_rate: float,
    n_shifts: float,
    p_select: np.ndarray | None = None,
    n_shifts_select: float | None = None,
) -> ThresholdReport:
    """Compute both thresholds and the alarm-budget curve between them.

    `p_select` / `n_shifts_select` are the scores the budget threshold is CHOSEN on
    (validation, ADR-061); `y` / `p` are what it is MEASURED on (test). Omitting them keeps
    the old behaviour of choosing on the evaluation scores themselves, which is test-set
    selection when those are test scores -- train.py no longer does that.

    The rule is the legacy top-k: the k-th highest selection score. When that score is a
    tied plateau every tied unit alerts, which is how a 6/shift budget ran to 8.48/shift.
    `budget_alerts_at_selection` and `score_count_at_budget_threshold` report it.
    """
    p_cost = cost_optimal_threshold(econ, escape_rate)
    sel = p if p_select is None else np.asarray(p_select)
    n_sel = n_shifts if n_shifts_select is None else n_shifts_select

    # Budget threshold: the cut that admits at most `max_alerts_per_shift` per shift.
    budget_total = econ.max_alerts_per_shift * n_sel
    order = np.sort(sel)[::-1]
    k = int(min(max(1, round(budget_total)), len(order)))
    p_budget = float(order[k - 1])

    prec_b, rec_b, n_b = _rates(y, p, p_budget)
    prec_c, rec_c, n_c = _rates(y, p, p_cost)

    alerts_shift_budget = n_b / n_shifts
    alerts_shift_cost = n_c / n_shifts

    per_year_scale = (
        econ.production["operating_days_per_year"] * econ.production["shifts_per_day"]
    ) / max(1e-9, n_shifts)

    # The gap against the cost-optimal threshold is real but NOT an actionable number:
    # at p* the model flags essentially every unit, so "recall 100%" is arithmetic, not
    # an operating policy a plant could run. Quoting it as value forgone would be
    # indefensible the moment a judge asks who answers 431 alerts a shift.
    theoretical_gap = (
        max(0.0, rec_c - rec_b) * float(y.sum()) * cost_of_miss(econ, escape_rate)
        * per_year_scale
    )

    # What a manager can actually decide: the marginal value of staffing enough response
    # capacity to double the alert budget. This IS a real choice with a real price.
    doubled = int(min(max(1, round(budget_total * 2)), len(order)))
    # The doubled-budget point is chosen on the selection scores too; its recall is
    # measured on the evaluation scores like everything else.
    p_doubled = float(order[doubled - 1])
    _, rec_2x, n_2x = _rates(y, p, p_doubled)
    extra_caught = max(0.0, rec_2x - rec_b) * float(y.sum())
    marginal_value = extra_caught * cost_of_miss(econ, escape_rate) * per_year_scale
    marginal_cost = (
        (n_2x - n_b) * econ.cost("supervisor_response_cost_per_alarm") * per_year_scale
    )

    curve = []
    for thr in np.unique(np.quantile(p, np.linspace(0.50, 0.9999, 60))):
        pr, rc, nf = _rates(y, p, float(thr))
        curve.append(
            {
                "threshold": float(thr),
                "alerts_per_shift": nf / n_shifts,
                "precision": pr,
                "recall": rc,
            }
        )

    return ThresholdReport(
        cost_optimal=float(p_cost),
        budget_constrained=p_budget,
        alerts_per_shift_at_cost_optimal=float(alerts_shift_cost),
        alerts_per_shift_at_budget=float(alerts_shift_budget),
        precision_at_budget=float(prec_b),
        recall_at_budget=float(rec_b),
        recall_at_cost_optimal=float(rec_c),
        theoretical_gap_per_year=float(theoretical_gap),
        doubled_budget_threshold=p_doubled,
        doubled_budget_recall=float(rec_2x),
        marginal_value_of_doubling=float(marginal_value),
        marginal_cost_of_doubling=float(marginal_cost),
        units_per_shift=float(units_per_shift),
        curve=curve,
        threshold_selected_on="test" if p_select is None else "validation",
        budget_alerts_intended=k,
        budget_alerts_at_selection=int((sel >= p_budget).sum()),
        score_count_at_budget_threshold=int((p == p_budget).sum()),
    )
