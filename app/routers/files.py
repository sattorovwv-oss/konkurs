from urllib.parse import quote
from fastapi import APIRouter, Depends
from starlette.responses import Response
from app.db import get_db
from app.models import Payment, Submission
from app.services.errors import DomainError
from app.services.storage import Storage
from app.utils.security import require_user, is_admin

router = APIRouter()


@router.get("/files/{kind}/{item_id}")
def download(kind: str, item_id: int, user=Depends(require_user), db=Depends(get_db)):
    if kind == "receipt":
        obj = db.get(Payment, item_id)
        key = obj.receipt_path if obj else None
        name = obj.original_name if obj else ""
        mime = obj.mime_type if obj else "application/octet-stream"
    elif kind == "source":
        obj = db.get(Submission, item_id)
        key = obj.source_file_path if obj else None
        name = obj.source_original_name if obj else ""
        mime = "application/zip"
    else:
        raise DomainError("Файл не найден.", 404)
    if not obj or (not is_admin(user) and obj.participation.user_id != user.id):
        raise DomainError("Файл не найден.", 404)
    content = Storage().read(key)
    return Response(
        content,
        media_type=mime,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe=""),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
