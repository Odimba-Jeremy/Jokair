"""Accounts module: models, schemas, password hashing, sessions, rate-limiting and auth routes."""
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from pwdlib import PasswordHash
from sqlalchemy import ForeignKey, Integer, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from config import settings
from database import Base, get_db
from i18n import get_lang_from_header, translate

router = APIRouter(prefix="/auth", tags=["auth"])
password_hasher = PasswordHash.recommended()
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]

# In-memory failed attempts tracker: key -> list of failure timestamps
_failed_attempts: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(key: str, max_attempts: int, window: int) -> bool:
    if settings.app_env.lower() in {"development", "local"}:
        return False
    now = time.time()
    _failed_attempts[key] = [t for t in _failed_attempts[key] if now - t < window]
    return len(_failed_attempts[key]) >= max_attempts


def _record_failed_attempt(key: str) -> None:
    _failed_attempts[key].append(time.time())


def _clear_failed_attempts(key: str) -> None:
    _failed_attempts.pop(key, None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    user: Mapped[User] = relationship(back_populates="sessions")


class RegisterInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: Password
    password_confirmation: Password


class LoginInput(BaseModel):
    email: EmailStr
    password: Password


class UpdateProfileInput(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    current_password: Password | None = None
    new_password: Password | None = None


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: EmailStr


class AuthOutput(BaseModel):
    user: UserOutput
    csrf_token: str
    access_token: str


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _require_csrf(
    request: Request,
    header_token: str | None,
    cookie_token: str | None,
    lang: str = "fr",
) -> str:
    # Local-only development bypass. Production keeps origin and CSRF validation.
    if settings.app_env.lower() in {"development", "local"}:
        return header_token or cookie_token or ""

    # Verify origin if sent by browser
    origin = request.headers.get("origin")
    if origin:
        normalized_origin = origin.rstrip("/")
        allowed = {o.rstrip("/") for o in settings.frontend_origins}
        allowed.add(str(request.base_url).rstrip("/"))
        local_frontend = re.fullmatch(
            r"https?://(?:localhost|127\.0\.0\.1)(?::(?:5500|8000|8001))?",
            normalized_origin,
        )
        if normalized_origin not in allowed and not local_frontend:
            raise HTTPException(status_code=403, detail=translate("csrf_invalid", lang))

    # Explicit bearer credentials are not attached automatically by browsers, so CSRF cookies are unnecessary.
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return header_token or ""

    if header_token and cookie_token and hmac.compare_digest(header_token, cookie_token):
        return cookie_token

    # Login and registration have no authenticated session. Exact Origin validation and CORS preflight
    # protect these JSON requests when a browser blocks third-party cookies.
    if request.url.path.endswith(("/auth/register", "/auth/login")) and header_token and origin:
        return header_token

    raise HTTPException(status_code=403, detail=translate("csrf_invalid", lang))


def _find_session(db: Session, raw_token: str | None) -> UserSession | None:
    if not raw_token:
        return None
    session = db.scalar(select(UserSession).where(UserSession.token_hash == _hash_token(raw_token)))
    if session is not None and session.expires_at <= int(time.time()):
        db.delete(session)
        db.commit()
        return None
    return session


def _set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    common = {
        "secure": settings.cookie_secure,
        "samesite": "none" if settings.cookie_secure else "lax",
        "path": "/",
        "max_age": settings.session_days * 24 * 60 * 60,
    }
    response.set_cookie(settings.session_cookie_name, session_token, httponly=True, **common)
    response.set_cookie(settings.csrf_cookie_name, csrf_token, httponly=False, **common)


def _create_session(db: Session, user: User, response: Response) -> tuple[str, str]:
    session_token = _new_token()
    csrf_token = _new_token()
    db.add(UserSession(
        user_id=user.id,
        token_hash=_hash_token(session_token),
        csrf_hash=_hash_token(csrf_token),
        expires_at=int(time.time()) + settings.session_days * 24 * 60 * 60,
    ))
    db.commit()
    _set_auth_cookies(response, session_token, csrf_token)
    return session_token, csrf_token


@router.get("/csrf")
def csrf_token(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None, alias=settings.session_cookie_name),
):
    token = request.cookies.get(settings.csrf_cookie_name)
    active_session = _find_session(db, session_token)
    if active_session is None or not token or not hmac.compare_digest(active_session.csrf_hash, _hash_token(token)):
        token = _new_token()
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
        path="/",
        max_age=settings.session_days * 24 * 60 * 60,
    )
    return {"csrf_token": token}


@router.post("/register", response_model=AuthOutput, status_code=201)
def register(
    payload: RegisterInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    _require_csrf(request, x_csrf_token, csrf_cookie, lang)
    if payload.password != payload.password_confirmation:
        raise HTTPException(status_code=422, detail=translate("password_mismatch", lang))
    user = User(
        name=payload.name.strip(),
        email=str(payload.email).strip().lower(),
        password_hash=password_hasher.hash(payload.password),
        created_at=int(time.time()),
    )
    db.add(user)
    try:
        db.flush()
        access_token, token = _create_session(db, user, response)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=translate("email_in_use", lang)) from None
    return AuthOutput(user=user, csrf_token=token, access_token=access_token)


@router.post("/login", response_model=AuthOutput)
def login(
    payload: LoginInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    _require_csrf(request, x_csrf_token, csrf_cookie, lang)
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{payload.email.strip().lower()}"

    if _check_rate_limit(rate_key, settings.rate_limit_login_attempts, settings.rate_limit_window_seconds):
        raise HTTPException(status_code=429, detail=translate("rate_limited", lang))

    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not password_hasher.verify(payload.password, user.password_hash):
        _record_failed_attempt(rate_key)
        raise HTTPException(status_code=401, detail=translate("invalid_credentials", lang))

    _clear_failed_attempts(rate_key)
    access_token, token = _create_session(db, user, response)
    return AuthOutput(user=user, csrf_token=token, access_token=access_token)


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None, alias=settings.session_cookie_name),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> User:
    lang = get_lang_from_header(accept_language)
    authorization = request.headers.get("authorization", "")
    bearer_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
    session = _find_session(db, bearer_token or session_token)
    user = db.get(User, session.user_id) if session else None
    if user is None:
        raise HTTPException(status_code=401, detail=translate("auth_required", lang))
    return user


@router.get("/me", response_model=UserOutput)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOutput)
def update_profile(
    payload: UpdateProfileInput,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    _require_csrf(request, x_csrf_token, csrf_cookie, lang)

    if payload.name:
        user.name = payload.name.strip()

    if payload.new_password:
        if not payload.current_password or not password_hasher.verify(payload.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail=translate("invalid_credentials", lang))
        user.password_hash = password_hasher.hash(payload.new_password)

    db.commit()
    db.refresh(user)
    return user


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    session_token: str | None = Cookie(default=None, alias=settings.session_cookie_name),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias=settings.csrf_cookie_name),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
):
    lang = get_lang_from_header(accept_language)
    token = _require_csrf(request, x_csrf_token, csrf_cookie, lang)
    authorization = request.headers.get("authorization", "")
    bearer_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
    session = _find_session(db, bearer_token or session_token)
    if session is None or not hmac.compare_digest(session.csrf_hash, _hash_token(token)):
        raise HTTPException(status_code=403, detail=translate("csrf_invalid", lang))
    db.delete(session)
    db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
