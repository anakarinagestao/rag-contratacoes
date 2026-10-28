from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.database import init_db, initialize_database
from app.core.security import dummy_password_hash, validate_security_settings
from app.core.logging import RagPerfMiddleware
from app.core.faiss_startup import validar_faiss_no_startup
from app.core.normalized_startup import validar_normalizada_no_startup
from app.modules.admin.routes import router as admin_router
from app.modules.auth.routes import router as auth_router
from app.modules.documents.routes import router as documents_router
from app.modules.query.routes import router as query_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_security_settings()
    dummy_password_hash()
    raw_connection = initialize_database()
    raw_connection.close()
    init_db()
    validar_normalizada_no_startup(app)
    validar_faiss_no_startup(app)
    yield


app = FastAPI(
    title="RAG - Contratações Públicas",
    lifespan=lifespan,
)
app.add_middleware(RagPerfMiddleware)

app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(admin_router)
app.include_router(query_router)


@app.get("/")
def root():
    return {
        "message": "API funcionando"
    }
