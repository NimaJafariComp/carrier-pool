"""Ranking evaluation and deadhead-ablation contracts."""

from pathlib import Path

from carrier_pool.decisioning.ranking_evaluation import (
    RankingEvaluationCase,
    evaluate_rankings,
    write_ranking_artifacts,
)


def test_metrics_and_ablation_are_reported_with_case_counts(tmp_path: Path) -> None:
    cases = (
        RankingEvaluationCase("t1", "booked", ("booked", "other"), "RICH"),
        RankingEvaluationCase("t1", "booked", ("other", "booked"), "SPARSE"),
    )
    report = evaluate_rankings(cases, (("near", "booked", ("booked", "other"), "RICH"),))

    assert report.with_deadhead.top_1_recall == "0.5"
    assert report.without_deadhead.case_count == 1
    assert report.with_deadhead.by_history_depth["RICH"].case_count == 1
    assert report.with_deadhead.by_history_depth["SPARSE"].case_count == 1
    path = write_ranking_artifacts(report, tmp_path)
    assert path.name == "ranking_metrics.json"
    assert "eventually booked carrier is only a weak behavioral proxy" in path.read_text()
