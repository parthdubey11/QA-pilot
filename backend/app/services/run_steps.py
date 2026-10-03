from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.models.run import Run, RunStep, StepKind


def storage_root() -> Path:
    return Path(get_settings().storage_dir).resolve()


class StepRecorder:
    """Appends numbered run_steps for one run (the worker is the only writer, so a counter is enough)."""

    def __init__(self, run: Run, start_index: int = 0):
        assert run.id is not None
        self.run = run
        self.next_index = start_index

    @classmethod
    async def for_run(cls, run: Run) -> "StepRecorder":
        last = await RunStep.find(RunStep.run_id == run.id).sort(-RunStep.index).first_or_none()
        return cls(run, 0 if last is None else last.index + 1)

    async def add(self, kind: StepKind, message: str, **fields: Any) -> RunStep:
        assert self.run.id is not None
        step = RunStep(run_id=self.run.id, index=self.next_index, kind=kind, message=message, **fields)
        self.next_index += 1
        await step.insert()
        return step

    def screenshot_path(self) -> tuple[Path, str]:
        """(absolute path to write, path relative to STORAGE_DIR to store) for the next step's screenshot."""
        relative = f"runs/{self.run.id}/step-{self.next_index:04d}.png"
        return storage_root() / relative, relative
