"""
Hard safety limits for the debugging loop (spec section 4).

Part 3 intentionally owns none of this — it can be called as many times as
a caller likes. Part 4 is the component responsible for making sure an LLM
can never drive an unbounded investigation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    """Bounds on one `diagnose` invocation.

    `max_actions_per_iteration` is already enforced independently by Part 3
    (`MAX_ACTIONS_PER_PLAN = 10` in `tracecli_llm.schemas`) and is not
    re-implemented here; the remaining knobs are Part 4's own.
    """

    max_planner_iterations: int = 3
    max_total_evidence: int = 200
    max_wall_seconds: float = 90.0
