from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from app.db import get_db
from app.models import LoginSession
from app.services import telegram_auth
from app.utils.security import current_user, safe_form
from app.utils.web import render, redirect

router = APIRouter()


@router.get("/login")
def login(request: Request, user=Depends(current_user)):
    return redirect(request, "/profile") if user else render(request, "login.html")


@router.get("/auth/telegram/start")
def start(request: Request, db=Depends(get_db)):
    return redirect(request, telegram_auth.start_login(db, request.session))


@router.get("/auth/telegram/callback")
async def callback(request: Request, code: str = "", state: str = "", db=Depends(get_db)):
    verifier = await run_in_threadpool(telegram_auth.consume_attempt, db, request.session, state)
    claims = await telegram_auth.exchange_code(code, verifier)
    await run_in_threadpool(telegram_auth.finish_login, db, request.session, claims)
    return redirect(request, "/profile", "Вы вошли через Telegram.")


@router.post("/logout")
def logout(request: Request, form=Depends(safe_form), db=Depends(get_db)):
    token = request.session.get("sid")
    if token:
        row = db.get(LoginSession, telegram_auth.digest(token))
        if row:
            db.delete(row)
            db.commit()
    request.session.clear()
    return redirect(request, "/", "Вы вышли из аккаунта.")
