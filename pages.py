"""Image uploads, dedication management (full CRUD) and published dedication pages."""
import html
import re
import time
import unicodedata
import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, JSON, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from accounts import User, _require_csrf, current_user
from config import settings
from database import Base, get_db
from i18n import get_lang_from_header, translate
from storage import is_valid_user_image, save_image

router = APIRouter(tags=["pages"])
public_router = APIRouter(tags=["published-pages"])


class Dedication(Base):
    __tablename__ = "dedications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    template_name: Mapped[str] = mapped_column(String(50), nullable=False, default="dedicate")
    form_fields: Mapped[dict] = mapped_column(JSON, nullable=False)
    images: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PublishInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    recipient_name: str = Field(default="", max_length=100)
    template_name: str = Field(default="dedicate", max_length=50)
    form_fields: dict[str, str] = Field(default_factory=dict)
    images: dict[str, str] = Field(default_factory=dict)
    custom_slug: str = Field(default="", max_length=80)


class UpdatePageInput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    recipient_name: str | None = Field(default=None, max_length=100)
    template_name: str | None = Field(default=None, max_length=50)
    form_fields: dict[str, str] | None = None
    images: dict[str, str] | None = None
    custom_slug: str | None = Field(default=None, max_length=80)


def _field(fields: dict, name: str, default: str = "") -> str:
    return str(fields.get(f"data[{name}]", fields.get(name, default)) or default)


def _slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:65].strip("-") or "dedication"


def _render_page(title: str, recipient: str, fields: dict, images: dict, template: str = "dedicate", lang: str = "fr") -> str:
    safe_title = html.escape(title)
    safe_recipient = html.escape(recipient)
    intro = html.escape(_field(fields, "intro_message"), quote=False).replace("\n", "<br>")
    quote = html.escape(_field(fields, "quote"), quote=False)
    letter = html.escape(_field(fields, "letter_content"), quote=False).replace("\n", "<br>")
    closing = html.escape(_field(fields, "end_message"), quote=False).replace("\n", "<br>")
    music_url = _field(fields, "music_url")

    sources = ([images["cover_image"]] if images.get("cover_image") else [])
    sources.extend(value for key, value in sorted(images.items()) if key.startswith("image_") and value)
    
    gallery = "".join(
        f'<img src="{html.escape(src, quote=True)}" alt="Photo souvenir" loading="lazy" class="gallery-photo">'
        for src in sources[:21]
    )
    image_section = f'<section class="gallery">{gallery}</section>' if gallery else ""

    music_section = ""
    if music_url and (music_url.startswith("http://") or music_url.startswith("https://") or music_url.startswith("/uploads/")):
        music_section = f'''<div class="audio-box">
            <audio controls loop preload="metadata">
                <source src="{html.escape(music_url, quote=True)}">
            </audio>
        </div>'''

    is_en = lang == "en"
    tagline = "A special dedication" if is_en else "Une dédicace spéciale"
    for_text = f"For <strong>{safe_recipient or 'someone special'}</strong>" if is_en else f"Pour <strong>{safe_recipient or 'une personne spéciale'}</strong>"
    footer_text = closing if closing else ("Created with Dedikka" if is_en else "Créé avec Dedikka")
    html_lang = "en" if is_en else "fr"

    return f"""<!doctype html>
<html lang="{html_lang}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{safe_title} · Dedikka</title>
    <meta name="description" content="{safe_title}">
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            background: linear-gradient(135deg, #fff1f2 0%, #fdf4ff 50%, #f5f3ff 100%);
            color: #3b2042;
            font: 17px/1.75 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            min-height: 100vh;
        }}
        main {{ max-width: 820px; margin: 4vh auto; padding: 20px; }}
        article {{
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 28px;
            padding: clamp(24px, 6vw, 64px);
            box-shadow: 0 25px 60px -15px rgba(120, 53, 115, 0.15);
            text-align: center;
        }}
        .tagline {{ text-transform: uppercase; letter-spacing: 2px; font-size: 0.82rem; color: #a21caf; font-weight: 700; }}
        h1 {{ font-size: clamp(2.2rem, 7vw, 4rem); line-height: 1.15; color: #b42370; margin: 12px 0 16px; font-weight: 800; }}
        .recipient {{ font-size: 1.25rem; color: #581c87; margin-bottom: 28px; }}
        .message {{ text-align: left; white-space: pre-wrap; margin: 28px auto; max-width: 660px; line-height: 1.8; }}
        .quote {{
            font-size: 1.35rem; font-style: italic; color: #6b21a8; background: #faf5ff;
            border-left: 4px solid #c084fc; padding: 20px 24px; border-radius: 0 16px 16px 0;
            margin: 32px auto; max-width: 660px; text-align: left;
        }}
        .gallery {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 14px;
            margin: 36px 0;
        }}
        .gallery img {{
            width: 100%;
            height: 240px;
            object-fit: cover;
            border-radius: 18px;
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}
        .gallery img:hover {{
            transform: scale(1.03);
            box-shadow: 0 12px 24px -6px rgba(0, 0, 0, 0.2);
        }}
        .audio-box {{ margin: 28px auto; display: flex; justify-content: center; }}
        audio {{ border-radius: 30px; outline: none; }}
        footer {{
            margin-top: 40px;
            padding-top: 24px;
            border-top: 1px solid #f3e8ff;
            color: #86198f;
            font-size: 0.95rem;
        }}
    </style>
</head>
<body>
    <main>
        <article>
            <p class="tagline">{tagline}</p>
            <h1>{safe_title}</h1>
            <p class="recipient">{for_text}</p>
            {music_section}
            {f'<div class="message">{intro}</div>' if intro else ''}
            {f'<blockquote class="quote">“{quote}”</blockquote>' if quote else ''}
            {image_section}
            {f'<div class="message">{letter}</div>' if letter else ''}
            <footer>{footer_text}</footer>
        </article>
    </main>
</body>
</html>"""


@router.post("/uploads/images", tags=["storage"])
async def upload_image(
    file: Annotated[UploadFile, File()],
    request: Request,
    user: User = Depends(current_user),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    _require_csrf(request, x_csrf_token, csrf_cookie, lang)
    return await save_image(file, user.id, str(request.base_url), lang)


@router.post("/pages/preview", response_class=HTMLResponse, tags=["pages"])
async def preview_page(
    request: Request,
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    form = await request.form()
    fields = {key: str(value) for key, value in form.multi_items() if not hasattr(value, "filename")}
    title = fields.get("data[custom_celebration_title]") or fields.get("data[title]") or fields.get("title") or ("A special dedication" if lang == "en" else "Une dédicace spéciale")
    recipient = str(form.get("recipient_name") or fields.get("data[recipient_name]") or "")
    template_name = str(form.get("template_name") or "dedicate")

    images = {}
    cover = form.get("preview_cover_image")
    if isinstance(cover, str) and (cover.startswith("data:image/") or cover.startswith("http://") or cover.startswith("https://") or cover.startswith("/uploads/")):
        images["cover_image"] = cover

    for key, value in form.multi_items():
        match = re.fullmatch(r"preview_image_url\[(\d+)\]", key)
        if match and isinstance(value, str) and (value.startswith("data:image/") or value.startswith("http://") or value.startswith("https://") or value.startswith("/uploads/")):
            images[f"image_{match.group(1)}"] = value

    return HTMLResponse(_render_page(title, recipient, fields, images, template_name, lang))


@router.post("/pages", status_code=201, tags=["pages"])
def publish_page(
    payload: PublishInput,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    _require_csrf(request, x_csrf_token, csrf_cookie, lang)

    if len(payload.form_fields) > 300 or any(len(k) > 150 or len(v) > 6000 for k, v in payload.form_fields.items()):
        raise HTTPException(status_code=422, detail=translate("page_data_too_large", lang))

    base_url = str(request.base_url)
    if len(payload.images) > 21 or any(not is_valid_user_image(url, user.id, base_url) for url in payload.images.values()):
        raise HTTPException(status_code=422, detail=translate("image_ownership_invalid", lang))

    slug = _slugify(payload.custom_slug or payload.title)
    if db.scalar(select(Dedication.id).where(Dedication.slug == slug)):
        if payload.custom_slug:
            raise HTTPException(status_code=409, detail=translate("slug_in_use", lang))
        slug = f"{slug}-{uuid.uuid4().hex[:7]}"

    now = int(time.time())
    page = Dedication(
        user_id=user.id,
        slug=slug,
        title=payload.title.strip(),
        recipient_name=payload.recipient_name.strip(),
        template_name=payload.template_name.strip() or "dedicate",
        form_fields=payload.form_fields,
        images=payload.images,
        created_at=now,
        updated_at=now,
    )
    db.add(page)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=translate("slug_in_use", lang)) from None

    base = settings.public_backend_url.rstrip("/") if settings.public_backend_url else str(request.base_url).rstrip("/")
    return {
        "id": page.id,
        "slug": slug,
        "url": f"{base}/p/{slug}",
        "title": page.title,
        "recipient_name": page.recipient_name,
        "template_name": page.template_name,
    }


@router.get("/pages/mine", tags=["pages"])
def my_pages(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    request: Request = None,
):
    pages = db.scalars(select(Dedication).where(Dedication.user_id == user.id).order_by(Dedication.created_at.desc())).all()
    base = settings.public_backend_url.rstrip("/") if settings.public_backend_url else (str(request.base_url).rstrip("/") if request else "")
    return [{
        "id": page.id,
        "slug": page.slug,
        "title": page.title,
        "recipient_name": page.recipient_name,
        "template_name": page.template_name,
        "url": f"{base}/p/{page.slug}" if base else f"/p/{page.slug}",
        "created_at": page.created_at,
        "updated_at": page.updated_at,
    } for page in pages]


@router.get("/pages/{page_id}", tags=["pages"])
def get_page(
    page_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    page = db.get(Dedication, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail=translate("page_not_found", lang))
    if page.user_id != user.id:
        raise HTTPException(status_code=403, detail=translate("page_forbidden", lang))

    base = settings.public_backend_url.rstrip("/") if settings.public_backend_url else str(request.base_url).rstrip("/")
    return {
        "id": page.id,
        "slug": page.slug,
        "title": page.title,
        "recipient_name": page.recipient_name,
        "template_name": page.template_name,
        "form_fields": page.form_fields,
        "images": page.images,
        "url": f"{base}/p/{page.slug}",
        "created_at": page.created_at,
        "updated_at": page.updated_at,
    }


@router.patch("/pages/{page_id}", tags=["pages"])
def update_page(
    page_id: int,
    payload: UpdatePageInput,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    _require_csrf(request, x_csrf_token, csrf_cookie, lang)

    page = db.get(Dedication, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail=translate("page_not_found", lang))
    if page.user_id != user.id:
        raise HTTPException(status_code=403, detail=translate("page_forbidden", lang))

    base_url = str(request.base_url)
    if payload.title is not None:
        page.title = payload.title.strip()
    if payload.recipient_name is not None:
        page.recipient_name = payload.recipient_name.strip()
    if payload.template_name is not None:
        page.template_name = payload.template_name.strip()
    if payload.form_fields is not None:
        if len(payload.form_fields) > 300 or any(len(k) > 150 or len(v) > 6000 for k, v in payload.form_fields.items()):
            raise HTTPException(status_code=422, detail=translate("page_data_too_large", lang))
        page.form_fields = payload.form_fields
    if payload.images is not None:
        if len(payload.images) > 21 or any(not is_valid_user_image(url, user.id, base_url) for url in payload.images.values()):
            raise HTTPException(status_code=422, detail=translate("image_ownership_invalid", lang))
        page.images = payload.images

    if payload.custom_slug:
        new_slug = _slugify(payload.custom_slug)
        existing = db.scalar(select(Dedication.id).where(Dedication.slug == new_slug, Dedication.id != page.id))
        if existing:
            raise HTTPException(status_code=409, detail=translate("slug_in_use", lang))
        page.slug = new_slug

    page.updated_at = int(time.time())
    db.commit()
    db.refresh(page)

    base = settings.public_backend_url.rstrip("/") if settings.public_backend_url else str(request.base_url).rstrip("/")
    return {
        "id": page.id,
        "slug": page.slug,
        "url": f"{base}/p/{page.slug}",
        "title": page.title,
        "recipient_name": page.recipient_name,
        "template_name": page.template_name,
        "form_fields": page.form_fields,
        "images": page.images,
        "updated_at": page.updated_at,
    }


@router.delete("/pages/{page_id}", tags=["pages"])
def delete_page(
    page_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    _require_csrf(request, x_csrf_token, csrf_cookie, lang)

    page = db.get(Dedication, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail=translate("page_not_found", lang))
    if page.user_id != user.id:
        raise HTTPException(status_code=403, detail=translate("page_forbidden", lang))

    db.delete(page)
    db.commit()
    return {"status": "ok", "message": translate("page_deleted", lang)}


@public_router.get("/p/{slug}", response_class=HTMLResponse, tags=["pages"])
def view_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    lang: str | None = None,
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    page = db.scalar(select(Dedication).where(Dedication.slug == slug))
    preferred_lang = "en" if lang == "en" else get_lang_from_header(accept_language)
    if page is None:
        raise HTTPException(status_code=404, detail=translate("page_not_found", preferred_lang))
    return HTMLResponse(_render_page(page.title, page.recipient_name, page.form_fields, page.images, page.template_name, preferred_lang))
