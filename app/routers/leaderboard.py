from fastapi import APIRouter, Depends, Request
from app.db import get_db
from app.routers.contests import visible_contest
from app.services.ranking import ranking
from app.utils.security import current_user
from app.utils.web import render

router = APIRouter()


@router.get("/contests/{slug}/leaderboard")
def leaderboard(request: Request, slug: str, user=Depends(current_user), db=Depends(get_db)):
    c = visible_contest(db, slug, user)
    return render(request, "leaderboard.html", user, c=c, rows=ranking(db, c) if c.results_published else [])
