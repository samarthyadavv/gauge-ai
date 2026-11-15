"""Durable DB-backed worker. Ambiguous interrupted generations are never retried automatically."""

import logging, time
from datetime import timedelta
from sqlalchemy import select, update
from .db import Session, Run, Result, now, init_db
from .provider import generate
from .grading import grade
from .config import settings

logger = logging.getLogger("ai-eval.worker")


def recover_stale():
    with Session.begin() as s:
        s.execute(
            update(Run)
            .where(
                Run.status == "running", Run.heartbeat < now() - timedelta(minutes=3)
            )
            .values(
                status="interrupted",
                finished=now(),
                error="Worker heartbeat expired. Partial results retained; start a new run explicitly.",
            )
        )


def claim():
    with Session.begin() as s:
        query = (
            select(Run.id).where(Run.status == "queued").order_by(Run.created).limit(1)
        )
        if s.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        rid = s.scalar(query)
        if not rid:
            return None
        result = s.execute(
            update(Run)
            .where(Run.id == rid, Run.status == "queued")
            .values(status="running", started=now(), heartbeat=now())
        )
        return rid if result.rowcount else None


def execute(rid):
    with Session() as s:
        run = s.get(Run, rid)
    for i, case in enumerate(run.snapshot):
        with Session.begin() as s:
            current = s.get(Run, rid)
            if current.status != "running":
                return
            if current.cancel_requested:
                current.status = "cancelled"
                current.finished = now()
                return
            current.heartbeat = now()
        start = time.perf_counter()
        try:
            result = generate(run, case["prompt"])
            assessment = grade(result["output"], case)
            row = Result(run_id=rid, position=i, **result, **assessment)
        except Exception as exc:
            # Never store provider error bodies: they can echo evaluated prompts.
            row = Result(
                run_id=rid,
                position=i,
                output=None,
                correct=False,
                forbidden_hit=False,
                passed=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                error="Provider request failed ("
                + type(exc).__name__
                + "). Usage may have been billed.",
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
            )
        with Session.begin() as s:
            current = s.get(Run, rid)
            if current.status != "running":
                return
            s.add(row)
            current.heartbeat = now()
    with Session.begin() as s:
        current = s.get(Run, rid)
        if current.status == "running":
            current.status = "cancelled" if current.cancel_requested else "completed"
            current.finished = now()


def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            recover_stale()
            rid = claim()
            if rid:
                execute(rid)
            else:
                time.sleep(settings().worker_poll_seconds)
        except Exception as exc:
            logger.error("worker_cycle_failed type=%s", type(exc).__name__)
            time.sleep(2)


if __name__ == "__main__":
    main()
