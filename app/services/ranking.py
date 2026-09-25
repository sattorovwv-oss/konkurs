from dataclasses import dataclass
from decimal import Decimal
from sqlalchemy import select
from app.models import Submission, Participation
from app.utils.time import aware

CRITERIA = ("functionality", "ui", "code_quality", "stability", "originality")


@dataclass
class RankRow:
    submission: Submission
    breakdown: dict
    total: Decimal
    rank: int = 0
    prize: Decimal = Decimal(0)

    @property
    def participant(self):
        return self.submission.participation


def rank_submissions(submissions, contest):
    rows = []
    for sub in submissions:
        if not sub.scores:
            continue
        # Decimal averages preserve exact ordering before display rounding.
        values = {key: sum(Decimal(getattr(s, key)) for s in sub.scores) / len(sub.scores) for key in CRITERIA}
        total = sum(Decimal(sum(getattr(s, key) for key in CRITERIA)) for s in sub.scores) / len(sub.scores)
        rows.append(RankRow(submission=sub, breakdown=values, total=total))
    rows.sort(
        key=lambda r: (
            -r.total,
            -r.breakdown["functionality"],
            -r.breakdown["code_quality"],
            -r.breakdown["stability"],
            -r.breakdown["originality"],
            aware(r.submission.submitted_at),
            r.submission.id,
        )
    )
    prizes = [contest.prize_first_tjs, contest.prize_second_tjs, contest.prize_third_tjs]
    for index, row in enumerate(rows):
        row.rank = index + 1
        row.prize = prizes[index] if index < 3 else Decimal(0)
    return rows


def get_submissions(db, contest_id):
    return list(
        db.scalars(
            select(Submission).join(Participation).where(Participation.contest_id == contest_id).order_by(Submission.id)
        )
    )


def ranking(db, contest):
    return rank_submissions(get_submissions(db, contest.id), contest)
