"""Upload validation and file storage (Cloudinary)."""

import io
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import cloudinary
import cloudinary.uploader
import requests
from fastapi import UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pypdf import PdfReader

from app import config


ALLOWED_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/heic",
    "image/heif",
    "application/pdf",
}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".pdf"}

_EXTENSION_MIME_TYPES = {
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
}


class UploadRejected(Exception):
    def __init__(self, message: str, status_code: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class FileFetchFailed(Exception):
    pass


@dataclass(frozen=True)
class FilePart:
    """File bytes plus the MIME type to declare when sending them to an LLM."""

    data: bytes
    mime_type: str


@dataclass(frozen=True)
class ValidatedUpload:
    file: FilePart
    extension: str


def init_storage() -> None:
    cloudinary.config(
        cloud_name=config.CLOUDINARY_CLOUD_NAME,
        api_key=config.CLOUDINARY_API_KEY,
        api_secret=config.CLOUDINARY_API_SECRET,
    )


async def read_validated_upload(upload: UploadFile) -> ValidatedUpload:
    extension = os.path.splitext(upload.filename or "")[1].lower()
    content_type = upload.content_type or ""

    # iPhones sometimes send HEIC as application/octet-stream, so the extension also counts.
    if content_type not in ALLOWED_CONTENT_TYPES and extension not in ALLOWED_EXTENSIONS:
        raise UploadRejected(f"許可されていないファイル形式です。({content_type} / {extension})")

    data = await upload.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadRejected(
            "ファイルサイズが大きすぎます(上限10MB)。",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        )

    if extension in _EXTENSION_MIME_TYPES:
        mime_type = _EXTENSION_MIME_TYPES[extension]
    elif content_type in ALLOWED_CONTENT_TYPES:
        mime_type = content_type
    else:
        mime_type = "image/jpeg"

    if mime_type == "application/pdf":
        pages = await run_in_threadpool(_count_pdf_pages, data)
        if pages is None:
            raise UploadRejected("PDFを読み込めませんでした。画像で送るか、別のPDFをお試しください。")
        if pages > config.MAX_PDF_PAGES:
            raise UploadRejected(f"PDFは{config.MAX_PDF_PAGES}ページまでです。")

    return ValidatedUpload(file=FilePart(data=data, mime_type=mime_type), extension=extension)


def _count_pdf_pages(data: bytes) -> int | None:
    """Page count, or None if the file is not a readable PDF."""
    try:
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # pypdf raises many types on broken or hostile files
        return None


async def store_upload(upload: ValidatedUpload) -> str:
    """Store the file and return its public URL. Images (HEIC included) are converted to JPEG;
    PDFs keep all their pages."""
    file_format = "pdf" if upload.extension == ".pdf" else "jpg"
    # The Cloudinary SDK is blocking; keep it off the event loop.
    result = await run_in_threadpool(cloudinary.uploader.upload, upload.file.data, format=file_format)
    return result["secure_url"]


def _ensure_storage_url(url: str) -> None:
    # Only fetch from our own storage host, so a tampered URL cannot make the server
    # request arbitrary addresses (SSRF).
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise FileFetchFailed(f"unexpected URL scheme: {parsed.scheme}")
    host = parsed.hostname or ""
    if host != "cloudinary.com" and not host.endswith(".cloudinary.com"):
        raise FileFetchFailed(f"host is not allowed: {host}")


async def fetch_stored_file(url: str) -> FilePart:
    _ensure_storage_url(url)
    try:
        response = await run_in_threadpool(requests.get, url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FileFetchFailed(str(exc)) from exc

    mime_type = response.headers.get("Content-Type", "image/jpeg").split(";")[0]
    return FilePart(data=response.content, mime_type=mime_type)
