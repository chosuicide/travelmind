import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from app.core.config import (
    GENERATION_STALE_AFTER_MINUTES,
    GENERATION_WORKER_POLL_SECONDS,
)
from app.db import models
from app.db.session import SessionLocal
from app.generation.service import (
    run_generation_task,
)


logger = logging.getLogger(__name__)


# === 持久任务恢复：中断任务重新入队，由 LangGraph 检查点继续 ===
# 流程：查找超时 running → queued → 相同 thread_id → 从最近节点继续
def recover_stale_runs(stale_after: timedelta) -> int:
    cutoff = datetime.now(timezone.utc) - stale_after
    recovered = 0

    with SessionLocal() as db:
        stale_runs = (
            db.query(models.GenerationRun)
            .filter(
                models.GenerationRun.status == "running",
                models.GenerationRun.started_at.is_not(None),
                models.GenerationRun.started_at < cutoff,
            )
            .all()
        )
        for run in stale_runs:
            run.status = "queued"
            run.error_code = "ResumingFromCheckpoint"
            run.error_message = (
                "Generation worker stopped; queued to resume from checkpoint"
            )
            run.started_at = None
            run.finished_at = None
            trip = db.get(models.Trip, run.trip_id)
            if trip is not None and trip.status == "generating":
                trip.status = "generating"
            recovered += 1
        db.commit()

    if recovered:
        logger.warning("Recovered %s stale generation run(s)", recovered)
    return recovered


# === 数据库队列 Worker：每次读取最早 queued，由 Service 原子领取 ===
# 流程：查最早任务 → 原子 queued→running → Agent → succeeded/failed
def run_worker_once() -> bool:
    with SessionLocal() as db:
        run_id = (
            db.query(models.GenerationRun.id)
            .filter(models.GenerationRun.status == "queued")
            .order_by(
                models.GenerationRun.created_at.asc(),
                models.GenerationRun.id.asc(),
            )
            .limit(1)
            .scalar()
        )

    if run_id is None:
        return False
    return run_generation_task(run_id)


def run_worker(
    poll_seconds: float = GENERATION_WORKER_POLL_SECONDS,
    stale_after_minutes: int = GENERATION_STALE_AFTER_MINUTES,
) -> None:
    recover_stale_runs(timedelta(minutes=stale_after_minutes))
    logger.info("TravelMind generation worker started")

    while True:
        handled = run_worker_once()
        if not handled:
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process TravelMind generation tasks",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process at most one queued task and exit",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=GENERATION_WORKER_POLL_SECONDS,
    )
    parser.add_argument(
        "--stale-after-minutes",
        type=int,
        default=GENERATION_STALE_AFTER_MINUTES,
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.once:
        recover_stale_runs(timedelta(minutes=args.stale_after_minutes))
        run_worker_once()
        return
    run_worker(
        poll_seconds=args.poll_seconds,
        stale_after_minutes=args.stale_after_minutes,
    )


if __name__ == "__main__":
    main()
