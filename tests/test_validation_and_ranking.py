from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.schemas.forms import ContestForm, ScoreForm, SubmissionForm
from app.services.ranking import rank_submissions
from app.utils.time import from_local, local_display, now


def valid_contest():
    return dict(
        title="Bot Battle",
        slug="bot-battle",
        summary="Create a Telegram bot",
        full_task="Full task description",
        rules="Contest rules",
        max_participants=10,
        fee_tjs="50",
        prize_first_tjs="200",
        prize_second_tjs="100",
        prize_third_tjs="50",
        registration_deadline="2030-01-01T11:30",
        starts_at="2030-01-01T12:30",
        ends_at="2030-01-02T12:30",
    )


def test_dushanbe_roundtrip():
    assert from_local("2030-01-01T12:30").hour == 7
    assert local_display(from_local("2030-01-01T12:30")).endswith("12:30")
    assert ContestForm(**valid_contest()).starts_at.hour == 7


@pytest.mark.parametrize(
    "field,value",
    [
        ("starts_at", "2030-01-01T10:30"),
        ("ends_at", "2030-01-01T12:30"),
        ("slug", "Bad Slug"),
        ("fee_tjs", "-1"),
        ("fee_tjs", "NaN"),
        ("max_participants", 0),
    ],
)
def test_contest_validation(field, value):
    data = valid_contest()
    data[field] = value
    with pytest.raises(ValidationError):
        ContestForm(**data)


@pytest.mark.parametrize(
    "field,maximum", [("functionality", 40), ("ui", 20), ("code_quality", 20), ("stability", 10), ("originality", 10)]
)
def test_scores_boundaries(field, maximum):
    data = dict(functionality=0, ui=0, code_quality=0, stability=0, originality=0)
    for invalid in [-1, maximum + 1, 1.5]:
        data[field] = invalid
        with pytest.raises(ValidationError):
            ScoreForm(**data)
    data[field] = maximum
    assert getattr(ScoreForm(**data), field) == maximum


def score(f=35, u=18, c=18, s=9, o=9):
    return SimpleNamespace(functionality=f, ui=u, code_quality=c, stability=s, originality=o)


def sub(id, scores, seconds=0):
    return SimpleNamespace(id=id, scores=scores, submitted_at=now() + timedelta(seconds=seconds))


def contest():
    return SimpleNamespace(prize_first_tjs=Decimal(200), prize_second_tjs=Decimal(100), prize_third_tjs=Decimal(50))


def test_average_and_prizes():
    rows = rank_submissions(
        [sub(1, [score(), score(40, 20, 20, 10, 10)]), sub(2, [score(40, 20, 20, 10, 10)]), sub(3, [])], contest()
    )
    assert [r.submission.id for r in rows] == [2, 1]
    assert rows[1].total == Decimal("94.5")
    assert rows[1].prize == 100


@pytest.mark.parametrize(
    "better,worse",
    [
        (score(36, 17, 18, 9, 9), score(35, 18, 18, 9, 9)),
        (score(35, 17, 19, 9, 9), score(35, 18, 18, 9, 9)),
        (score(35, 17, 18, 10, 9), score(35, 18, 18, 9, 9)),
        (score(35, 17, 18, 9, 10), score(35, 18, 18, 9, 9)),
    ],
)
def test_all_tie_break_criteria(better, worse):
    rows = rank_submissions([sub(1, [worse], -100), sub(2, [better], 100)], contest())
    assert rows[0].total == rows[1].total
    assert rows[0].submission.id == 2


def test_tie_break_last_submission_time():
    assert rank_submissions([sub(1, [score()], 20), sub(2, [score()], -20)], contest())[0].submission.id == 2


def test_submission_bot_and_optional_github():
    assert (
        SubmissionForm(bot_username="@useful_bot", description="Useful project description", github_url="").github_url
        is None
    )
    for name in ["javascript:alert(1)", "not_a_bot/anything", "abc", "ab cb ot"]:
        with pytest.raises(ValidationError):
            SubmissionForm(bot_username=name, description="long description")
