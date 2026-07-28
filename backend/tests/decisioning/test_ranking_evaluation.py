"""Ranking evaluation and deadhead-ablation contracts."""

from pathlib import Path

from carrier_pool.decisioning.ranking_evaluation import (
    RankingEvaluationCase,
    evaluate_rankings,
    ranking_acceptance_failures,
    write_ranking_artifacts,
    write_ranking_formula_comparison,
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


def test_formula_comparison_uses_same_case_reports(tmp_path: Path) -> None:
    cases = (RankingEvaluationCase("t1", "booked", ("booked",), "RICH"),)
    path = write_ranking_formula_comparison(
        evaluate_rankings(cases, cases),
        evaluate_rankings(cases, cases),
        tmp_path,
        evaluate_rankings(cases, cases),
    )

    payload = path.read_text()
    assert path.name == "ranking_score_comparison.json"
    assert '"candidate"' in payload
    assert '"legacy"' in payload
    assert '"same_case_population": true' in payload
    assert '"calibrated_candidate_model_version": "carrier-ranking-v6"' in payload
    assert '"all_same_case_population": true' in payload


def test_acceptance_requires_coverage_and_separation_not_proxy_recall() -> None:
    tags = (
        "NEAR_EXACT",
        "BROADER_LANE",
        "DISTANCE_EQUIPMENT",
        "LIMITED_CANDIDATE",
        "CLOSE_SCORE_TIE",
    )
    cases = tuple(
        RankingEvaluationCase(
            "t1",
            "booked",
            ("booked", "other"),
            "RICH" if index < 4 else "SPARSE",
            top_fit_is_tied=index == 0,
            supported_candidate_count=2,
            coverage_tags=(
                "RICH" if index < 4 else "SPARSE",
                tags[index % len(tags)],
            ),
            source_system=("FREIGHTFLOW", "HAULDESK", "BROKEROS")[index % 3],
        )
        for index in range(24)
    )
    report = evaluate_rankings(cases, cases)

    assert ranking_acceptance_failures(report) == ()


def test_supported_only_metrics_keep_limited_booking_labels_out_of_recall() -> None:
    case = RankingEvaluationCase(
        "t1",
        "limited-booked",
        ("supported", "limited-booked"),
        "SPARSE",
        supported_candidate_count=1,
        supported_ranked_carrier_ids=("supported",),
        supported_no_rank_reason="BOOKED_CARRIER_LIMITED_RELEVANT_HISTORY",
        source_system="FREIGHTFLOW",
    )

    report = evaluate_rankings((case,), (case,), {"without_lane": (case,)})

    assert report.with_deadhead.scored_case_count == 0
    assert report.with_deadhead.no_rank_reason_counts == {
        "BOOKED_CARRIER_LIMITED_RELEVANT_HISTORY": 1
    }
    assert report.all_candidates_with_deadhead.scored_case_count == 1
    assert report.all_candidates_with_deadhead.top_3_recall == "1"
    assert report.component_ablations["without_lane"].case_count == 1
    assert report.weight_tuning_eligible is False
    assert report.weight_tuning_blockers
