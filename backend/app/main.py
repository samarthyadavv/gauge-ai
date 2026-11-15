import hashlib, json, secrets, time, uuid, logging
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta,timezone
from pathlib import Path
from typing import Annotated, Literal
from fastapi import FastAPI, Depends, Request, HTTPException, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, delete, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from .config import settings
from .db import db, Session, Dataset, Run, Result, SessionToken, now, init_db
from .grading import summarize, load_json

cfg = settings()
logger = logging.getLogger("ai-eval")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    with Session() as s:
        s.execute(select(Dataset.id).limit(1))
    yield


app = FastAPI(
    title="Gauge AI Evaluation API",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
DB = Annotated[DBSession, Depends(db)]
login_attempts = defaultdict(deque)
run_attempts = deque()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def authorize(request: Request, s: DBSession):
    token = request.headers.get("authorization", "")
    if token.startswith("Bearer ") and secrets.compare_digest(
        digest(token[7:]), digest(cfg.admin_key)
    ):
        return "api"
    cookie = request.cookies.get("gauge_session", "")
    session = (
        s.scalar(
            select(SessionToken).where(
                SessionToken.digest == digest(cookie),
                SessionToken.expires > now(),
                SessionToken.key_digest == digest(cfg.admin_key),
            )
        )
        if cookie
        else None
    )
    if not session:
        raise HTTPException(401, "Sign in required")
    if request.method not in ("GET", "HEAD", "OPTIONS") and not secrets.compare_digest(
        request.headers.get("x-csrf-token", ""), session.csrf
    ):
        raise HTTPException(403, "Invalid CSRF token")
    return session


@app.middleware("http")
async def protection(request: Request, call_next):
    rid = str(uuid.uuid4())
    start = time.monotonic()
    if request.method in ("POST", "PATCH", "DELETE", "PUT"):
        if request.headers.get("origin") and request.headers["origin"] != cfg.base_url:
            return JSONResponse({"detail": "Untrusted origin"}, status_code=403)
        size = 0
        parts = []
        async for part in request.stream():
            size += len(part)
            if size > 1000000:
                return JSONResponse(
                    {"detail": "Maximum request size is 1 MB"}, status_code=413
                )
            parts.append(part)
        request._body = b"".join(parts)
    response = await call_next(request)
    response.headers.update(
        {
            "X-Request-ID": rid,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }
    )
    if cfg.base_url.startswith("https://"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    logger.info(
        json.dumps(
            {
                "request_id": rid,
                "method": request.method,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - start) * 1000),
            }
        )
    )
    return response


class Login(BaseModel):
    key: str = Field(min_length=1, max_length=512)


@app.post("/api/session")
def login(body: Login, request: Request, response: Response, s: DB):
    # Trust the socket peer, not a caller-provided X-Forwarded-For header.
    ip = request.client.host if request.client else "unknown"
    attempts = login_attempts[ip]
    while attempts and attempts[0] < time.monotonic() - 60:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(429, "Too many sign-in attempts; wait one minute")
    attempts.append(time.monotonic())
    if not secrets.compare_digest(digest(body.key), digest(cfg.admin_key)):
        raise HTTPException(401, "Incorrect access key")
    token = secrets.token_urlsafe(40)
    csrf = secrets.token_urlsafe(32)
    s.execute(delete(SessionToken).where(SessionToken.expires < now()))
    s.add(
        SessionToken(
            digest=digest(token),
            key_digest=digest(cfg.admin_key),
            csrf=csrf,
            expires=now() + timedelta(hours=8),
        )
    )
    s.commit()
    response.set_cookie(
        "gauge_session",
        token,
        httponly=True,
        secure=cfg.base_url.startswith("https://"),
        samesite="lax",
        max_age=28800,
        path="/",
    )
    return {"csrf": csrf}


@app.delete("/api/session")
def logout(request: Request, response: Response, s: DB):
    authorize(request, s)
    s.execute(
        delete(SessionToken).where(
            SessionToken.digest == digest(request.cookies.get("gauge_session", ""))
        )
    )
    s.commit()
    response.delete_cookie("gauge_session")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request, s: DB):
    session = authorize(request, s)
    return {
        "csrf": session.csrf if isinstance(session, SessionToken) else None,
        "providers": [
            {
                "id": "mock",
                "name": "Deterministic mock",
                "models": ["deterministic-v1"],
            },
            *(
                [
                    {
                        "id": "openai",
                        "name": "OpenAI",
                        "models": [
                            v.strip() for v in cfg.openai_models.split(",") if v.strip()
                        ],
                    }
                ]
                if cfg.openai_api_key
                else []
            ),
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready(s: DB):
    s.execute(text("SELECT 1"))
    return {"status": "ready"}


class Case(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    expected: str = Field(min_length=1, max_length=4000)
    match: Literal["exact", "contains", "json"] = "exact"
    forbidden: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_expected(self):
        if any(not x.strip() or len(x) > 200 for x in self.forbidden):
            raise ValueError("Forbidden terms must contain 1–200 characters")
        if self.match == "json":
            try:
                load_json(self.expected)
            except ValueError:
                raise ValueError("Expected value must be valid JSON for JSON matching")
        return self


class DatasetInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    cases: list[Case] = Field(min_length=1, max_length=100)


@app.post("/api/datasets", status_code=201)
def create_dataset(body: DatasetInput, request: Request, s: DB):
    authorize(request, s)
    cases = [c.model_dump() for c in body.cases]
    fingerprint = digest(
        json.dumps(cases, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    dataset = Dataset(
        name=body.name,
        description=body.description,
        cases=cases,
        fingerprint=fingerprint,
    )
    s.add(dataset)
    s.commit()
    return {"id": dataset.id, "fingerprint": fingerprint}


@app.get("/api/datasets")
def datasets(request: Request, s: DB, offset: int = 0):
    authorize(request, s)
    if offset < 0:
        raise HTTPException(422, "Offset must be positive")
    return [
        {
            "id": d.id,
            "name": d.name,
            "description": d.description,
            "count": len(d.cases),
            "fingerprint": d.fingerprint,
            "created": timestamp(d.created),
        }
        for d in s.scalars(
            select(Dataset).order_by(Dataset.created.desc()).offset(offset).limit(100)
        )
    ]


@app.get("/api/datasets/{did}")
def dataset(did: str, request: Request, s: DB):
    authorize(request, s)
    d = s.get(Dataset, did)
    if not d:
        raise HTTPException(404, "Dataset not found")
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "fingerprint": d.fingerprint,
        "cases": d.cases,
    }


class RunInput(BaseModel):
    dataset_id: str
    name: str = Field(min_length=1, max_length=100)
    provider: Literal["mock", "openai"] = "mock"
    model: str = Field(default="deterministic-v1", max_length=100)
    instruction: str = Field(default="", max_length=2000)
    max_tokens: int = Field(default=256, ge=16, le=1000)
    input_price: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    output_price: float | None = Field(
        default=None, ge=0, le=10000, allow_inf_nan=False
    )


# One operator's explicit request can be retried safely after a network interruption.
@app.post("/api/runs", status_code=201)
def create_run(body: RunInput, request: Request, s: DB):
    authorize(request, s)
    # Serialize admission across API processes; row claims remain independent.
    if s.bind.dialect.name == "postgresql":
        s.execute(text("SELECT pg_advisory_xact_lock(61924001)"))
    else:
        s.connection().exec_driver_sql("BEGIN IMMEDIATE")
    key = request.headers.get("idempotency-key", "")
    if not key or len(key) > 100:
        raise HTTPException(
            422, "An Idempotency-Key header of 1–100 characters is required"
        )
    payload_digest = digest(json.dumps(body.model_dump(), sort_keys=True))
    existing = s.scalar(select(Run).where(Run.idempotency_key == key))
    if existing:
        if existing.request_digest != payload_digest:
            raise HTTPException(409, "Idempotency key belongs to a different request")
        return {"id": existing.id, "duplicate": True}
    dataset = s.get(Dataset, body.dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    allowed = (
        ["deterministic-v1"]
        if body.provider == "mock"
        else (
            [x.strip() for x in cfg.openai_models.split(",")]
            if cfg.openai_api_key
            else []
        )
    )
    if body.model not in allowed:
        raise HTTPException(422, "Provider/model is not configured")
    while run_attempts and run_attempts[0] < time.monotonic() - 60:
        run_attempts.popleft()
    if len(run_attempts) >= 10:
        raise HTTPException(429, "Maximum 10 new runs per minute")
    if (
        s.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.status.in_(["queued", "running"]))
        )
        >= 8
    ):
        raise HTTPException(429, "Queue is full; wait for active runs")
    run = Run(
        **body.model_dump(),
        snapshot=dataset.cases,
        dataset_name=dataset.name,
        fingerprint=dataset.fingerprint,
        idempotency_key=key,
        request_digest=payload_digest
    )
    s.add(run)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        existing = s.scalar(select(Run).where(Run.idempotency_key == key))
        if not existing or existing.request_digest != payload_digest:
            raise HTTPException(409, "Conflicting concurrent request")
        return {"id": existing.id, "duplicate": True}
    run_attempts.append(time.monotonic())
    return {"id": run.id}


def timestamp(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value

def run_data(s, run, details=False):
    results = list(
        s.scalars(
            select(Result).where(Result.run_id == run.id).order_by(Result.position)
        )
    )
    result = {
        "id": run.id,
        "name": run.name,
        "dataset_id": run.dataset_id,
        "dataset_name": run.dataset_name,
        "fingerprint": run.fingerprint,
        "provider": run.provider,
        "model": run.model,
        "instruction": run.instruction,
        "max_tokens": run.max_tokens,
        "input_price": run.input_price,
        "output_price": run.output_price,
        "status": run.status,
        "cancel_requested": run.cancel_requested,
        "created": timestamp(run.created),
        "started": timestamp(run.started),
        "finished": timestamp(run.finished),
        "error": run.error,
        "summary": summarize(run, results),
    }
    if details:
        result["results"] = [
            {
                "position": r.position,
                "prompt": run.snapshot[r.position]["prompt"],
                "expected": run.snapshot[r.position]["expected"],
                "match": run.snapshot[r.position]["match"],
                "output": r.output,
                "correct": r.correct,
                "forbidden_hit": r.forbidden_hit,
                "passed": r.passed,
                "latency_ms": r.latency_ms,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": r.cost_usd,
                "error": r.error,
            }
            for r in results
        ]
    return result


@app.get("/api/runs")
def runs(request: Request, s: DB):
    authorize(request, s)
    return [
        run_data(s, r)
        for r in s.scalars(select(Run).order_by(Run.created.desc()).limit(100))
    ]


@app.get("/api/runs/{rid}")
def run_detail(rid: str, request: Request, s: DB):
    authorize(request, s)
    run = s.get(Run, rid)
    if not run:
        raise HTTPException(404, "Run not found")
    return run_data(s, run, True)


@app.post("/api/runs/{rid}/cancel")
def cancel(rid: str, request: Request, s: DB):
    authorize(request, s)
    run = s.get(Run, rid)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status == "queued":
        run.status = "cancelled"
        run.finished = now()
    elif run.status == "running":
        run.cancel_requested = True
    s.commit()
    return {"status": run.status, "cancel_requested": run.cancel_requested}


@app.get("/api/compare")
def compare(baseline: str, candidate: str, request: Request, s: DB):
    authorize(request, s)
    a = s.get(Run, baseline)
    b = s.get(Run, candidate)
    if not a or not b:
        raise HTTPException(404, "Run not found")
    if a.fingerprint != b.fingerprint:
        raise HTTPException(409, "Compare runs with the same dataset fingerprint")
    if a.status != "completed" or b.status != "completed":
        raise HTTPException(409, "Both runs must be completed before comparing")
    x = run_data(s, a, True)
    y = run_data(s, b, True)
    deltas = [
        {
            "position": old["position"],
            "prompt": old["prompt"],
            "baseline": old["passed"],
            "candidate": new["passed"],
            "baseline_output": old["output"],
            "candidate_output": new["output"],
        }
        for old, new in zip(x["results"], y["results"])
    ]
    return {
        "baseline": x,
        "candidate": y,
        "pass_rate_delta": y["summary"]["pass_rate"] - x["summary"]["pass_rate"],
        "regressions": sum(d["baseline"] and not d["candidate"] for d in deltas),
        "improvements": sum(not d["baseline"] and d["candidate"] for d in deltas),
        "cases": deltas,
    }


frontend = Path(__file__).resolve().parents[2] / "frontend/dist"
if frontend.exists():
    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(frontend / "index.html")
