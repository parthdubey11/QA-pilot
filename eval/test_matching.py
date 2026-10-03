"""Tests for the ground-truth matching (run: python -m pytest eval)."""

from matching import FUNCTIONAL, match_a11y, match_bug, score_a11y, score_bugs


def bug(title: str, actual: str = "", expected: str = "", steps: list[str] | None = None) -> dict:
    return {"title": title, "actual": actual, "expected": expected, "steps": steps or []}


def test_real_reports_from_qa_pilot_runs_match_their_planted_bug() -> None:
    # Titles and texts as written by the Reporter agent in live runs.
    assert match_bug(bug("Sign up accepts passwords shorter than 8 characters",
                         "The site displayed 'Account created' despite the invalid input.")) == "F08"
    assert match_bug(bug("Out-of-stock Wool Beanie can be added to cart")) == "F09"
    assert match_bug(bug("Sign up allows duplicate email addresses to create multiple accounts")) == "F02"
    assert match_bug(bug("Cart total not updated after removing an item",
                         "The row disappears but the total still shows $109.98")) == "F03"
    assert match_bug(bug("Cart accepts a negative quantity", "Quantity -2 gives a total of -$39.98")) == "F01"
    assert match_bug(bug("Sale price not applied in cart", "Cart shows $49.99 instead of $39.99")) == "F06"
    assert match_bug(bug("Contact form accepts an invalid email address", "not-an-email was accepted")) == "F04"
    assert match_bug(bug("Header cart badge does not update after adding a product", "The badge stays at 0")) == "F10"
    assert match_bug(bug("Price low to high sort is incorrect", "$109.99 is listed before $14.99")) == "F15"


def test_more_specific_bug_wins_and_unrelated_reports_are_false_positives() -> None:
    # "more than the available stock" must not be taken for the out-of-stock bug, and vice versa.
    assert match_bug(bug("Checkout allows ordering more than the available stock", "20 ordered, 8 left")) == "F14"
    assert match_bug(bug("Login page title is misspelled", "Shows 'Lgin'")) is None
    assert match_bug(bug("Product images load slowly")) is None


def test_scoring_counts_distinct_ids_and_precision() -> None:
    score, annotated = score_bugs([
        bug("Out-of-stock item can be added to cart"),
        bug("Out-of-stock beanie added to the cart again"),  # same planted bug twice
        bug("Footer text is grey"),  # not planted
    ])
    assert score.found == {"F09"} and score.true_positives == 2 and score.reported == 3
    assert round(score.recall, 3) == round(1 / len(FUNCTIONAL), 3) and round(score.precision or 0, 3) == 0.667
    assert [a["match"] for a in annotated] == ["F09", "F09", None]


def test_accessibility_matching_uses_rule_and_page() -> None:
    assert match_a11y("html-has-lang", "/contact") == "A01"
    assert match_a11y("html-has-lang", "/login") is None  # real violation elsewhere, but not planted
    assert match_a11y("image-alt", "/") == "A02" and match_a11y("image-alt", "/products/1") is None
    assert match_a11y("link-name", "/cart?x=1") == "A07"
    assert match_a11y("focus-not-visible", "/signup") == "A10"
    assert match_a11y("alt-text-quality", "/products/1") is None
    score, _ = score_a11y([("link-name", "/"), ("link-name", "/cart"), ("image-alt", "/"), ("alt-text-quality", "/")])
    assert score.found == {"A07", "A02"} and score.true_positives == 3 and score.reported == 4


def test_score_run_and_markdown_report() -> None:
    from run_eval import aggregate, markdown, score_run

    raw = {"run_id": "abcdef123456", "status": "completed", "stats": {"seconds": 600, "llm_calls": 40, "input_tokens": 50000,
                                                                       "output_tokens": 5000, "tests": 8, "passed": 5, "failed": 3},
           "bugs": [bug("Out-of-stock beanie can be added to cart"), bug("Footer text is grey")],
           "a11y_issues": [{"rule_id": "image-alt", "page_path": "/", "source": "axe"},
                           {"rule_id": "focus-not-visible", "page_path": "/", "source": "keyboard"},
                           {"rule_id": "region", "page_path": "/", "source": "axe"}]}
    scored = [score_run(raw)]
    assert scored[0]["functional"]["found"] == ["F09"] and scored[0]["functional"]["precision"] == 0.5
    assert scored[0]["a11y_qa_pilot"]["found"] == ["A02", "A10"] and scored[0]["a11y_axe_only"]["found"] == ["A02"]
    agg = aggregate(scored)
    assert agg["mean_tokens"] == 55000 and agg["a11y_qa_pilot"]["found_in_every_run"] == ["A02", "A10"]
    report = markdown({"started": "2026-10-01 12:00", "goal": "g", "max_tests": 8, "llm": "fake"}, scored, agg)
    assert "| Recall | 7% | 20% | 10% |" in report and "Footer text is grey → **no planted bug" in report
