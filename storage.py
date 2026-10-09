"""Storage service supporting Supabase Storage and local disk fallback."""
import os
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException, UploadFile

from config import settings
from i18n import Lang, translate

MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_TYPES: dict[str, tuple[str, tuple[bytes, ...]]] = {
    "image/jpeg": ("jpg", (b"\xff\xd8\xff",)),
    "image/png": ("png", (b"\x89PNG\r\n\x1a\n",)),
    "image/webp": ("webp", (b"RIFF",)),
    "image/gif": ("gif", (b"GIF87a", b"GIF89a")),
}


def get_public_image_prefix(base_url: str = "") -> str:
    """Returns the base URL prefix for images, depending on Supabase or local storage."""
    if settings.supabase_url and settings.supabase_secret_key:
        return f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{settings.supabase_bucket}/"
    base = (settings.public_backend_url or base_url).rstrip("/")
    return f"{base}/uploads/" if base else "/uploads/"


def is_valid_user_image(url: str, user_id: int, base_url: str = "") -> bool:
    """Checks whether an image URL belongs to the user's storage directory."""
    if not url:
        return False
    # Check Supabase prefix
    if settings.supabase_url:
        supa_prefix = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{settings.supabase_bucket}/{user_id}/"
        if url.startswith(supa_prefix):
            return True
    # Check local prefix
    local_prefix = f"/uploads/{user_id}/"
    if url.startswith(local_prefix):
        return True
    base = (settings.public_backend_url or base_url).rstrip("/")
    if base and url.startswith(f"{base}/uploads/{user_id}/"):
        return True
    # Allow inline data URLs for previews
    if url.startswith("data:image/"):
        return True
    return False


async def save_image(file: UploadFile, user_id: int, base_url: str = "", lang: Lang = "fr") -> dict[str, str]:
    """Validates and saves an image, returning its public URL and storage path."""
    content_type = (file.content_type or "").lower()
    spec = IMAGE_TYPES.get(content_type)
    if spec is None:
        raise HTTPException(status_code=415, detail=translate("image_format_unsupported", lang))

    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=translate("image_too_large", lang))

    if not any(data.startswith(sig) for sig in spec[1]):
        raise HTTPException(status_code=415, detail=translate("image_invalid_content", lang))

    if content_type == "image/webp" and data[8:12] != b"WEBP":
        raise HTTPException(status_code=415, detail=translate("image_webp_invalid", lang))

    filename = f"{uuid.uuid4().hex}.{spec[0]}"
    object_path = f"{user_id}/{filename}"

    # Supabase upload if credentials are provided
    if settings.supabase_url and settings.supabase_secret_key:
        endpoint = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{settings.supabase_bucket}/{object_path}"
        headers = {
            "apikey": settings.supabase_secret_key,
            "Authorization": f"Bearer {settings.supabase_secret_key}",
            "Content-Type": content_type,
            "x-upsert": "false",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(endpoint, content=data, headers=headers)
                if res.status_code not in (200, 201):
                    raise HTTPException(status_code=502, detail=translate("storage_upload_failed", lang))
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            raise HTTPException(status_code=502, detail=translate("storage_unreachable", lang)) from exc

        url = f"{get_public_image_prefix()}{object_path}"
        return {"url": url, "path": object_path}

    # Local fallback storage for dev / standalone deployment
    upload_root = Path(settings.upload_dir)
    user_dir = upload_root / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / filename
    with open(file_path, "wb") as f:
        f.write(data)

    base = (settings.public_backend_url or base_url).rstrip("/")
    url = f"{base}/uploads/{user_id}/{filename}" if base else f"/uploads/{user_id}/{filename}"
    return {"url": url, "path": object_path}
