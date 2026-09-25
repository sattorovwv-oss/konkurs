import io
import stat
import zipfile
import pytest
from pypdf import PdfWriter
from app.services.errors import DomainError
from app.services.storage import Storage, validate_zip
from conftest import png_bytes, zip_bytes


def test_receipt_and_zip_store_roundtrip():
    storage = Storage()
    file = storage.save(png_bytes(), "../../receipt.png", "image/png", "receipts")
    assert file.name == "receipt.png"
    assert storage.read(file.key) == png_bytes()
    archive = storage.save(zip_bytes(), "project.zip", "application/zip", "submissions")
    assert storage.read(archive.key) == zip_bytes()
    storage.delete(file.key)
    with pytest.raises(DomainError):
        storage.read(file.key)


@pytest.mark.parametrize(
    "data,name,mime",
    [
        (b"<script>evil</script>", "receipt.png", "image/png"),
        (png_bytes(), "receipt.png", "text/html"),
        (png_bytes(), "receipt.jpg", "image/jpeg"),
        (b"PK123", "project.zip", "application/zip"),
    ],
)
def test_reject_disguised_upload(data, name, mime):
    with pytest.raises(DomainError):
        Storage().save(data, name, mime, "submissions" if name.endswith(".zip") else "receipts")


@pytest.mark.parametrize("name", ["../main.py", "/etc/passwd", "C:/main.py", "folder/../../main.py", "..\\main.py"])
def test_zip_traversal(name):
    with pytest.raises(DomainError):
        validate_zip(zip_bytes(name))


def test_symlink_zip():
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        entry = zipfile.ZipInfo("link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "/etc/passwd")
    with pytest.raises(DomainError):
        validate_zip(target.getvalue())


def test_zip_bomb_and_size():
    with pytest.raises(DomainError):
        validate_zip(zip_bytes(data=b"0" * 1000000))
    with pytest.raises(DomainError):
        Storage().save(b"0" * (8 * 1024**2 + 1), "receipt.pdf", "application/pdf", "receipts")


def test_pdf_parser_validates_real_document():
    target = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(target)
    stored = Storage().save(target.getvalue(), "receipt.pdf", "application/pdf", "receipts")
    assert stored.mime == "application/pdf"
    with pytest.raises(DomainError):
        Storage().save(b"%PDF-1.7\ninvalid", "receipt.pdf", "application/pdf", "receipts")


@pytest.mark.parametrize("key", ["../../.env", "receipts/../../.env", "/etc/passwd", "submissions/project.zip"])
def test_private_storage_paths(key):
    with pytest.raises(DomainError):
        Storage().read(key)


def test_s3_adapter_roundtrip(monkeypatch):
    from botocore.stub import Stubber, ANY
    from botocore.response import StreamingBody
    from app.config import get_settings

    settings = get_settings()
    for key, value in {
        "storage_backend": "s3",
        "s3_bucket": "test-bucket",
        "s3_region": "us-east-1",
        "s3_access_key_id": "test-access-key",
        "s3_secret_access_key": "test-secret-key",
    }.items():
        monkeypatch.setattr(settings, key, value)
    storage = Storage()
    content = png_bytes()
    with Stubber(storage.client) as stub:
        stub.add_response(
            "put_object", {}, {"Bucket": "test-bucket", "Key": ANY, "Body": content, "ContentType": "image/png"}
        )
        file = storage.save(content, "receipt.png", "image/png", "receipts")
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(content), len(content))},
            {"Bucket": "test-bucket", "Key": file.key},
        )
        assert storage.read(file.key) == content
        stub.add_response("delete_object", {}, {"Bucket": "test-bucket", "Key": file.key})
        storage.delete(file.key)
        stub.assert_no_pending_responses()
