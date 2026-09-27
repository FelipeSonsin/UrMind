from app.services.review_resolution import review_resolution


def _vote(reviewer: str, decision: str = "confirm") -> dict:
    return {
        "reviewer": reviewer,
        "reviewer_role": "reviewer",
        "decision": decision,
        "corrected_class": None,
        "corrected_latitude": None,
        "corrected_longitude": None,
    }


def test_independent_matching_reviews_reach_consensus() -> None:
    rows = [_vote("a"), _vote("b")]
    result = review_resolution(rows)
    assert result["status"] == "consensus"
    assert result["selected"] == rows[-1]


def test_conflict_requires_adjudication() -> None:
    rows = [_vote("a"), _vote("b", "reject")]
    assert review_resolution(rows)["status"] == "conflicted"
    admin = {**_vote("admin"), "reviewer_role": "admin", "adjudicated": True}
    assert review_resolution([*rows, admin])["status"] == "adjudicated"
