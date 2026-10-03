"""Cron schedules (APScheduler inside the worker): each enabled schedule replays its project's saved tests.

Schedules live in MongoDB; `ScheduleSync.sync()` runs every few seconds and adds, updates or removes the
APScheduler jobs to match, so changes made in the dashboard apply without restarting the worker.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from beanie import PydanticObjectId

from app.models.base import utcnow
from app.models.project import Project
from app.models.saved_test import SavedTest, Schedule
from app.services.runs import ActiveRunError, start_run

logger = logging.getLogger("qa_pilot.scheduler")


async def fire_schedule(schedule_id: str) -> None:
    """Start a scheduled replay run (skipped, with a reason, if a run is active or there's nothing to replay)."""
    schedule = await Schedule.get(PydanticObjectId(schedule_id))
    if schedule is None or not schedule.enabled:
        return
    project = await Project.get(schedule.project_id)
    if project is None:
        return
    schedule.last_triggered_at = utcnow()
    if await SavedTest.find(SavedTest.project_id == project.id).count() == 0:
        schedule.last_skip_reason = "Skipped: the project has no saved tests yet."
    else:
        try:
            run = await start_run(project, created_by=schedule.created_by, kind="replay", trigger="schedule",
                                  schedule_id=schedule.id, goal=f"Scheduled replay: {schedule.name}")
            schedule.last_run_id, schedule.last_skip_reason = run.id, None
            logger.info("schedule %s started run %s", schedule.id, run.id)
        except ActiveRunError:
            schedule.last_skip_reason = "Skipped: another run of this project was still in progress."
    await schedule.save()


class ScheduleSync:
    def __init__(self, scheduler: AsyncIOScheduler):
        self.scheduler = scheduler
        self.jobs: dict[str, tuple[str, str]] = {}  # schedule id -> (cron, timezone) currently scheduled

    async def sync(self) -> None:
        enabled = await Schedule.find(Schedule.enabled == True).to_list()  # noqa: E712
        wanted = {str(s.id): (s.cron, s.timezone) for s in enabled}
        for schedule_id, spec in wanted.items():
            if self.jobs.get(schedule_id) == spec:
                continue
            try:
                trigger = CronTrigger.from_crontab(spec[0], timezone=spec[1])
            except ValueError:
                logger.warning("schedule %s has an invalid cron %r; ignored", schedule_id, spec[0])
                continue
            self.scheduler.add_job(fire_schedule, trigger, id=schedule_id, args=[schedule_id], replace_existing=True,
                                   coalesce=True, max_instances=1, misfire_grace_time=300)
            self.jobs[schedule_id] = spec
            logger.info("scheduled %s (%s %s)", schedule_id, *spec)
        for schedule_id in list(self.jobs):
            if schedule_id not in wanted:
                if self.scheduler.get_job(schedule_id):
                    self.scheduler.remove_job(schedule_id)
                del self.jobs[schedule_id]
                logger.info("unscheduled %s", schedule_id)


def start_scheduler(sync_seconds: float = 30.0) -> tuple[AsyncIOScheduler, ScheduleSync]:
    scheduler = AsyncIOScheduler(timezone="UTC")
    sync = ScheduleSync(scheduler)
    scheduler.add_job(sync.sync, "interval", seconds=sync_seconds, id="sync-schedules", next_run_time=utcnow(),
                      coalesce=True, max_instances=1)
    scheduler.start()
    return scheduler, sync
