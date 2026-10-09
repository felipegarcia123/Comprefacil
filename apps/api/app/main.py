from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import settings

app = FastAPI(title="POS Colombia API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix="/api/v1")


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception):
    import logging
    logging.getLogger(__name__).exception("Unhandled API error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor"})
