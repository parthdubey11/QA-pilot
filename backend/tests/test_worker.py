import asyncio
from datetime import timedelta

from pymongo.asynchronous.database import AsyncDatabase

from app.models.base import utcnow
from app.models.job import Job
from worker import main as worker


async def test_claim_takes_oldest_pending_job(database: AsyncDatabase) -> None:
    now = utcnow()
    await Job(type="run", created_at=now).insert()
    oldest = await Job(type="run", created_at=now - timedelta(minutes=5)).insert()
    await Job(type="run", status="done", created_at=now - timedelta(hours=1)).insert()

    claimed = await worker.claim_next_job()

    assert claimed is not None and claimed.id == oldest.id
    assert claimed.status == "running" and claimed.attempts == 1 and claimed.started_at is not None
    stored = await Job.get(oldest.id)
    assert stored is not None and stored.status == "running"


async def test_claim_returns_none_when_queue_empty(database: AsyncDatabase) -> None:
    await Job(type="run", status="running").insert()
    assert await worker.claim_next_job() is None


async def test_concurrent_claims_never_share_a_job(database: AsyncDatabase) -> None:
    await Job.insert_many([Job(type="run") for _ in range(5)])

    claimed = await asyncio.gather(*(worker.claim_next_job() for _ in range(8)))

    ids = [job.id for job in claimed if job is not None]
    assert len(ids) == 5 and len(set(ids)) == 5
    assert claimed.count(None) == 3


async def test_run_job_records_success_and_failure(database: AsyncDatabase) -> None:
    async def ok(_job: Job) -> None:
        return None

    async def boom(_job: Job) -> None:
        raise RuntimeError("browser crashed")

    handlers = {"ok": ok, "boom": boom}
    for job_type in ("ok", "boom", "unknown"):
        await Job(type=job_type).insert()
    for _ in range(3):
        job = await worker.claim_next_job()
        assert job is not None
        await worker.run_job(job, handlers)

    by_type = {job.type: job for job in await Job.find_all().to_list()}
    assert by_type["ok"].status == "done" and by_type["ok"].finished_at is not None
    assert by_type["boom"].status == "failed" and by_type["boom"].error == "RuntimeError: browser crashed"
    assert by_type["unknown"].status == "failed" and "No handler" in (by_type["unknown"].error or "")


async def test_work_loop_processes_jobs_then_waits(database: AsyncDatabase, monkeypatch) -> None:
    done: list[str] = []

    async def handle(job: Job) -> None:
        done.append(job.payload["name"])

    monkeypatch.setitem(worker.HANDLERS, "demo", handle)
    await Job(type="demo", payload={"name": "a"}).insert()
    await Job(type="demo", payload={"name": "b"}).insert()
    stop = asyncio.Event()

    task = asyncio.create_task(worker.work(stop, poll_seconds=0.05))
    for _ in range(100):
        if len(done) == 2:
            break
        await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert done == ["a", "b"]
