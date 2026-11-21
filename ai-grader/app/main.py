from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import SessionLocal, init_db
from .routers import api, pages
from .seed import seed_if_empty

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.seed_demo:
        with SessionLocal() as db:
            seed_if_empty(db)
    yield


app = FastAPI(
    title="AI Grader",
    description="AI grading of students' written work with personalised feedback. "
                "The AI grade is a suggestion; the teacher makes the final decision.",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(pages.router)
app.include_router(api.router)
