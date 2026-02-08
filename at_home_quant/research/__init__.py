from at_home_quant.research.models import ExperimentWindow, ModelReport
from at_home_quant.research.registry import (
    complete_experiment,
    leakage_issues,
    register_experiment,
)


def run_walk_forward_experiment(*args, **kwargs):
    # Lazy import avoids circular import with backtest.service during module loading.
    from at_home_quant.research.service import run_walk_forward_experiment as _run

    return _run(*args, **kwargs)

__all__ = [
    "ExperimentWindow",
    "ModelReport",
    "register_experiment",
    "complete_experiment",
    "leakage_issues",
    "run_walk_forward_experiment",
]
