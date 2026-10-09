from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from accounts import User, UserSession, router as accounts_router
from config import settings
from database import Base, engine
from pages import Dedication, public_router, router as pages_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Ensure local upload directory exists
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    if settings.app_env == "development":
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Static files for locally uploaded images
upload_path = Path(settings.upload_dir)
upload_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(upload_path)), name="uploads")

if settings.app_env.lower() in {"development", "local"}:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Requested-With", "Accept-Language"],
    )

app.include_router(accounts_router, prefix=settings.api_prefix)
app.include_router(pages_router, prefix=settings.api_prefix)
app.include_router(public_router)


@app.get(settings.api_prefix + "/health", tags=["health"])
def health():
    return {
        "status": "ok",
        "site": "Dedikka",
        "supported_languages": ["fr", "en"],
        "storage": "supabase" if (settings.supabase_url and settings.supabase_secret_key) else "local",
    }
