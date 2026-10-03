"""Background worker: claims jobs from the MongoDB `jobs` collection and runs them.

Jobs are claimed atomically (find_one_and_update pending -> running, oldest first), so several
workers can run side by side without picking the same job.
"""

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable

from pymongo import ReturnDocument

from app.core.config import get_settings
from app.core.db import close_db, init_db_with_retry
from app.models.base import utcnow
from app.models.job import Job
from app.models.run import Run
from worker.orchestrator import handle_run_job
from worker.scheduler import start_scheduler

logger = logging.getLogger("qa_pilot.worker")

JobHandler = Callable[[Job], Awaitable[None]]

# job type -> handler
HANDLERS: dict[str, JobHandler] = {"run": handle_run_job}
INTERRUPTED = "The worker stopped while this was running (restart or crash)."


async def recover_interrupted() -> int:
    """At startup, fail jobs/runs left 'running' by a previous worker process (assumes a single worker)."""
    now = utcnow()
    jobs = await Job.get_pymongo_collection().update_many(
        {"status": "running"}, {"$set": {"status": "failed", "error": INTERRUPTED, "finished_at": now}}
    )
    await Run.get_pymongo_collection().update_many(
        {"status": "running"}, {"$set": {"status": "failed", "error": INTERRUPTED, "finished_at": now}}
    )
    return jobs.modified_count


async def claim_next_job() -> Job | None:
    """Atomically move the oldest pending job to running and return it (None if the queue is empty)."""
    raw = await Job.get_pymongo_collection().find_one_and_update(
        {"status": "pending"},
        {"$set": {"status": "running", "started_at": utcnow()}, "$inc": {"attempts": 1}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
    return Job.model_validate(raw) if raw else None


async def run_job(job: Job, handlers: dict[str, JobHandler] | None = None) -> None:
    """Run one claimed job and record done/failed. Never raises."""
    handler = (HANDLERS if handlers is None else handlers).get(job.type)
    try:
        if handler is None:
            raise LookupError(f"No handler for job type {job.type!r}")
        await handler(job)
        job.status, job.error = "done", None
    except Exception as exc:  # noqa: BLE001 - a failing job must not stop the worker
        logger.exception("job %s (%s) failed", job.id, job.type)
        job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
    job.finished_at = utcnow()
    await job.save()


async def work(stop: asyncio.Event, poll_seconds: float) -> None:
    idle_logged = False
    while not stop.is_set():
        job = await claim_next_job()
        if job is None:
            if not idle_logged:
                logger.info("waiting for jobs")
                idle_logged = True
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
            continue
        idle_logged = False
        logger.info("claimed job %s (%s, attempt %d)", job.id, job.type, job.attempts)
        await run_job(job)


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows: Ctrl+C raises KeyboardInterrupt instead
            pass

    await init_db_with_retry()
    logger.info("connected to MongoDB")
    if recovered := await recover_interrupted():
        logger.warning("marked %d interrupted job(s) as failed", recovered)
    scheduler, _ = start_scheduler()
    logger.info("scheduler started (cron schedules are synced from MongoDB every 30 s)")
    try:
        await work(stop, get_settings().worker_poll_seconds)
    finally:
        scheduler.shutdown(wait=False)
        await close_db()
        logger.info("worker stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per LLM request is too noisy
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # the 30 s schedule sync is too noisy
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
