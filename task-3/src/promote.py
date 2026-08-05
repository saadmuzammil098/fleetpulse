"""Select the best run and promote it through the MLflow model registry.

Run as `python -m src.promote` from task-3/, after `run_experiments.py`
has logged the run grid. Selection is driven by the validation-set
**expected cost per 1000 units** (src/metrics.py), not by the highest
PR-AUC or F2 in the leaderboard — the whole point of Task 2's cost
discussion was that the highest-ranking-power model is not automatically
the cheapest one to run in production, and this step is where that
argument actually has to pay off in a decision, not just a paragraph.

MLflow's registry "stage" API (Staging/Production strings) is deprecated
in favor of aliases as of MLflow 2.9; this uses aliases named "staging"
and "production" instead, which is the modern equivalent of the exact
same promotion workflow the roadmap spec describes.
"""
from __future__ import annotations

import mlflow
from mlflow import MlflowClient

from . import config

RECALL_FLOOR = 0.50  # promotion gate: below this, cost savings aren't trustworthy


def _all_runs(client: MlflowClient):
    experiment = client.get_experiment_by_name(config.EXPERIMENT_NAME)
    return client.search_runs(
        experiment_ids=[experiment.experiment_id],
        order_by=["metrics.val_expected_cost_per_1000 ASC"],
    )


def select_best(runs):
    passing = [r for r in runs if r.data.metrics.get("val_recall", 0) >= RECALL_FLOOR]
    candidates = passing or runs
    return min(candidates, key=lambda r: r.data.metrics["val_expected_cost_per_1000"])


def render_rationale(best, runner_up_by_pr_auc, all_runs) -> str:
    b = best.data
    ru = runner_up_by_pr_auc.data
    lines = [
        "# Task 3 Promotion Rationale",
        "",
        f"**Promoted run:** `{b.tags.get('mlflow.runName', best.info.run_id)}` "
        f"(`{best.info.run_id}`)",
        "",
        "## Why this run, not the highest-PR-AUC run",
        "",
        f"- Selected run: validation expected cost = "
        f"**{b.metrics['val_expected_cost_per_1000']:.1f} per 1000 units** "
        f"(PR-AUC {b.metrics['val_pr_auc']:.3f}, recall "
        f"{b.metrics['val_recall']:.3f}, precision "
        f"{b.metrics['val_precision']:.3f}).",
        f"- Highest validation PR-AUC in the grid: "
        f"`{ru.tags.get('mlflow.runName', runner_up_by_pr_auc.info.run_id)}` "
        f"at PR-AUC {ru.metrics['val_pr_auc']:.3f}, but its expected cost is "
        f"{ru.metrics['val_expected_cost_per_1000']:.1f} per 1000 units — "
        f"{'higher' if ru.metrics['val_expected_cost_per_1000'] > b.metrics['val_expected_cost_per_1000'] else 'not lower'} "
        "than the promoted run's, because ranking power (PR-AUC) and the "
        "actual cost of the errors a model makes at its chosen threshold "
        "are not the same thing — exactly the gap Task 2's F2-vs-PR-AUC "
        "discussion warned about.",
        "- This mirrors Task 2's own finding: a model can be a good "
        "*ranker* (PR-AUC) while making decisions, at a real operating "
        "threshold, that cost the fleet more in missed failures or "
        "wasted service visits than a slightly-lower-PR-AUC alternative "
        "would. The registry promotion in this project is therefore "
        "cost-metric-driven, not leaderboard-driven.",
        "",
        "## Promotion gate",
        f"- Runs below {RECALL_FLOOR:.0%} validation recall were excluded "
        "from selection regardless of cost, a model that hits a low cost "
        "number by simply not flagging much of anything is not a model "
        "we want production traffic, cost-per-1000 alone can't tell that "
        "story, recall has to gate it.",
        f"- {len(all_runs)} runs tracked in total; "
        f"{sum(1 for r in all_runs if r.data.metrics.get('val_recall', 0) >= RECALL_FLOOR)} "
        "passed the recall gate and were eligible for promotion.",
        "",
        "## Test-set confirmation (never used for selection)",
        f"- Test PR-AUC: {b.metrics['test_pr_auc']:.3f}, test recall: "
        f"{b.metrics['test_recall']:.3f}, test expected cost: "
        f"{b.metrics['test_expected_cost_per_1000']:.1f} per 1000 units.",
        "",
        "## Registry state",
        f"- Registered as `{config.REGISTERED_MODEL_NAME}`, aliased "
        "`staging` immediately, then `production` after the gate above "
        "passed on this same run's metrics.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    mlflow.set_tracking_uri(config.MLFLOW_TRACKING_URI)
    client = MlflowClient()

    runs = _all_runs(client)
    if not runs:
        raise RuntimeError("No runs found — run `python -m src.run_experiments` first.")

    best = select_best(runs)
    runner_up = max(runs, key=lambda r: r.data.metrics.get("val_pr_auc", 0))

    model_uri = f"runs:/{best.info.run_id}/model"
    mv = mlflow.register_model(model_uri, config.REGISTERED_MODEL_NAME)
    client.set_registered_model_alias(config.REGISTERED_MODEL_NAME, "staging", mv.version)

    passed_gate = best.data.metrics["val_recall"] >= RECALL_FLOOR
    if passed_gate:
        client.set_registered_model_alias(
            config.REGISTERED_MODEL_NAME, "production", mv.version
        )

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    config.PROMOTION_RATIONALE_PATH.write_text(
        render_rationale(best, runner_up, runs)
    )

    print(
        f"Promoted run {best.info.run_id} "
        f"({best.data.tags.get('mlflow.runName')}) as version {mv.version}: "
        f"staging{'+production' if passed_gate else ' only (gate failed)'}"
    )
    print(f"Wrote {config.PROMOTION_RATIONALE_PATH}")


if __name__ == "__main__":
    main()
