import io
import logging
import re
import stat
import uuid
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from app.config import get_settings
from app.services.errors import DomainError

logger = logging.getLogger(__name__)
Image.MAX_IMAGE_PIXELS = 25_000_000


@dataclass
class StoredFile:
    key: str
    name: str
    mime: str


def safe_name(name):
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if c.isprintable() and c not in "\r\n")[:180]
    return name or "file"


def validate_zip(data):
    if not data.startswith(b"PK\x03\x04") or not zipfile.is_zipfile(io.BytesIO(data)):
        raise DomainError("Файл должен быть настоящим ZIP-архивом.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 5000:
                raise DomainError("ZIP пустой или содержит больше 5000 файлов.")
            total = 0
            names = set()
            for item in entries:
                path = PurePosixPath(item.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or ":" in item.filename or "\x00" in item.filename:
                    raise DomainError("ZIP содержит опасный путь.")
                if item.filename in names or stat.S_ISLNK(item.external_attr >> 16):
                    raise DomainError("ZIP содержит ссылки или дублирующиеся имена.")
                names.add(item.filename)
                if item.flag_bits & 1 or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise DomainError("Используйте ZIP без пароля, со сжатием Deflate или без сжатия.")
                total += item.file_size
                if item.file_size > 100 * 1024**2 or total > 200 * 1024**2:
                    raise DomainError("Распакованный ZIP не должен превышать 200 MB, один файл — 100 MB.")
                if item.file_size > max(item.compress_size, 1) * 250:
                    raise DomainError("Подозрительно высокая степень сжатия ZIP.")
            # CRC validation streams members into memory buffers; never extracts to disk.
            if archive.testzip():
                raise DomainError("ZIP повреждён. Создайте архив повторно.")
    except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError, ValueError) as exc:
        raise DomainError("Не удалось проверить ZIP. Создайте архив повторно.") from exc


def validate_receipt(data, extension, claimed_mime):
    formats = {
        ".jpg": ("JPEG", "image/jpeg"),
        ".jpeg": ("JPEG", "image/jpeg"),
        ".png": ("PNG", "image/png"),
        ".webp": ("WEBP", "image/webp"),
    }
    if extension == ".pdf":
        if claimed_mime != "application/pdf" or not data.startswith(b"%PDF-"):
            raise DomainError("Расширение, MIME и содержимое PDF не совпадают.")
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 20:
                raise DomainError("PDF должен содержать 1–20 страниц и быть без пароля.")
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError("PDF повреждён или не поддерживается.") from exc
        return "application/pdf"
    if extension not in formats:
        raise DomainError("Разрешены JPG, JPEG, PNG, WEBP и PDF.")
    expected_format, expected_mime = formats[extension]
    if claimed_mime != expected_mime:
        raise DomainError("MIME-тип не соответствует расширению изображения.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format != expected_format:
                    raise DomainError("Содержимое изображения не соответствует расширению.")
                picture.verify()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise DomainError("Изображение повреждено или имеет слишком большое разрешение.") from exc
    return expected_mime


class Storage:
    def __init__(self):
        self.settings = get_settings()
        self.root = self.settings.storage_root.resolve()
        self.client = None
        if self.settings.storage_backend == "s3":
            self.client = boto3.client(
                "s3",
                endpoint_url=self.settings.s3_endpoint_url or None,
                region_name=self.settings.s3_region,
                aws_access_key_id=self.settings.s3_access_key_id,
                aws_secret_access_key=self.settings.s3_secret_access_key,
                config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3}),
            )

    def checked_key(self, key):
        if not re.fullmatch(r"(receipts|submissions)/[a-f0-9]{32}\.(jpg|jpeg|png|webp|pdf|zip)", key):
            raise DomainError("Файл не найден.", 404)
        return key

    def local_path(self, key):
        path = (self.root / self.checked_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise DomainError("Доступ запрещён.", 403)
        return path

    def save(self, data, filename, claimed_mime, kind):
        settings = self.settings
        name = safe_name(filename)
        ext = PurePosixPath(name).suffix.lower()
        maximum = settings.max_source_mb if kind == "submissions" else settings.max_receipt_mb
        if not data or len(data) > maximum * 1024**2:
            raise DomainError(f"Выберите непустой файл до {maximum} MB.")
        if kind == "submissions":
            if ext != ".zip" or claimed_mime not in {
                "application/zip",
                "application/x-zip-compressed",
                "application/octet-stream",
            }:
                raise DomainError("Исходный код принимается только в ZIP.")
            validate_zip(data)
            mime = "application/zip"
        else:
            mime = validate_receipt(data, ext, claimed_mime)
        key = f"{kind}/{uuid.uuid4().hex}{ext}"
        if self.client:
            self.client.put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=mime)
        else:
            path = self.local_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as target:
                target.write(data)
            path.chmod(0o600)
        return StoredFile(key, name, mime)

    def read(self, key):
        self.checked_key(key)
        if self.client:
            result = self.client.get_object(Bucket=self.settings.s3_bucket, Key=key)
            try:
                return result["Body"].read()
            finally:
                result["Body"].close()
        try:
            return self.local_path(key).read_bytes()
        except FileNotFoundError as exc:
            raise DomainError("Файл недоступен. Обратитесь к организатору.", 404) from exc

    def delete(self, key):
        if not key:
            return
        try:
            self.checked_key(key)
            if self.client:
                self.client.delete_object(Bucket=self.settings.s3_bucket, Key=key)
            else:
                self.local_path(key).unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Storage cleanup failed: %s", type(exc).__name__)
