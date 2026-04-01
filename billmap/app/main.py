"""BillMap — AP Invoice Extraction Tool."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.config import settings
from app.db import init_db

app = FastAPI(title=settings.app_name, debug=settings.debug)

# Static files and templates
static_dir = Path(__file__).parent / "static"
templates_dir = Path(__file__).parent / "templates"
static_dir.mkdir(exist_ok=True)
templates_dir.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
templates = Jinja2Templates(directory=str(templates_dir))

# Register routes
from app.routes.invoices import router as invoices_router  # noqa: E402
from app.routes.templates import router as templates_router  # noqa: E402
from app.routes.connections import router as connections_router  # noqa: E402
from app.routes.settings import router as settings_router  # noqa: E402

app.include_router(invoices_router, prefix="/invoices", tags=["invoices"])
app.include_router(templates_router, prefix="/templates", tags=["templates"])
app.include_router(connections_router, prefix="/connections", tags=["connections"])
app.include_router(settings_router, prefix="/settings", tags=["settings"])


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/")
async def index():
    return {"app": settings.app_name, "mode": settings.mode, "status": "running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)
