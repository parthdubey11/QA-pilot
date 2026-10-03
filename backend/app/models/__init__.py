"""Beanie documents (one per MongoDB collection). Add every new document to DOCUMENT_MODELS."""

from beanie import Document

from app.models.accessibility import A11yAudit, A11yIssue
from app.models.bug import Bug
from app.models.job import Job
from app.models.project import Credential, Project
from app.models.run import Run, RunStep
from app.models.saved_test import Notification, SavedTest, Schedule
from app.models.test_case import TestCase
from app.models.user import User

DOCUMENT_MODELS: list[type[Document]] = [
    User, Project, Credential, Job, Run, RunStep, TestCase, Bug, A11yAudit, A11yIssue, SavedTest, Schedule, Notification,
]

__all__ = [
    "DOCUMENT_MODELS", "A11yAudit", "A11yIssue", "Bug", "Credential", "Job", "Notification", "Project", "Run",
    "RunStep", "SavedTest", "Schedule", "TestCase", "User",
]
