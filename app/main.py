from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.inputs import router as inputs_router
from app.config import settings
from app.db import engine


app = FastAPI(
    title="Maoxx OS API",
    version="0.1.0",
)

app.include_router(inputs_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "maoxx-api",
        "environment": settings.app_env,
    }


@app.get("/health/db")
def database_health() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text(
                    """
                    SELECT
                        current_database() AS database_name,
                        current_user AS database_user
                    """
                )
            ).mappings().one()

        return {
            "status": "ok",
            "database": str(result["database_name"]),
            "user": str(result["database_user"]),
        }

    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection unavailable",
        ) from exc
