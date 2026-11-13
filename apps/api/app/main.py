from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import router
from app.db.session import create_db_engine
from app.services.workflow import (
    ActionExecutionFailed,
    PermissionDenied,
    ResourceNotFound,
    WorkflowConflict,
)

app = FastAPI(title="AgentOps Workspace API", version="0.1.0")
app.include_router(router)


@app.exception_handler(ResourceNotFound)
def resource_not_found(_request: object, _exc: ResourceNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Resource not found"})


@app.exception_handler(WorkflowConflict)
def workflow_conflict(_request: object, exc: WorkflowConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(PermissionDenied)
def permission_denied(_request: object, _exc: PermissionDenied) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "Business permission required"})


@app.exception_handler(ActionExecutionFailed)
def action_execution_failed(_request: object, _exc: ActionExecutionFailed) -> JSONResponse:
    return JSONResponse(
        status_code=503, content={"detail": "Simulated action failed; failure was recorded"}
    )


@app.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def readiness() -> dict[str, str]:
    try:
        with create_db_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ok"}
