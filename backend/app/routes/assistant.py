"""Ask questions about a project's QA history (RAG over bugs, test results, a11y issues and heals)."""

from datetime import datetime, time, timezone

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.core.deps import CurrentUser
from app.models.assistant import AssistantQuestion, AssistantSource
from app.rag import get_knowledge_base
from app.routes.projects import get_owned_project
from app.schemas.assistant import AskRequest, AssistantAnswerRead
from worker.agents.assistant import ask
from worker.llm import LLMError, LLMQuotaError, LLMRateLimitError, get_provider

router = APIRouter(prefix="/projects/{project_id}/assistant", tags=["assistant"])

HISTORY_LIMIT = 20
EXCERPT_CHARS = 400


def answer_read(q: AssistantQuestion) -> AssistantAnswerRead:
    return AssistantAnswerRead.model_validate(q.model_dump(exclude={"id", "project_id", "user_id"}) | {"id": str(q.id)})


@router.get("", response_model=list[AssistantAnswerRead])
async def history(project_id: str, user: CurrentUser) -> list[AssistantAnswerRead]:
    """The user's latest questions about this project, newest first."""
    project = await get_owned_project(user, project_id)
    questions = await AssistantQuestion.find(AssistantQuestion.project_id == project.id,
                                             AssistantQuestion.user_id == user.id) \
        .sort(-AssistantQuestion.created_at).limit(HISTORY_LIMIT).to_list()
    return [answer_read(q) for q in questions]


@router.post("", response_model=AssistantAnswerRead, status_code=status.HTTP_201_CREATED)
async def ask_question(project_id: str, body: AskRequest, user: CurrentUser) -> AssistantAnswerRead:
    project = await get_owned_project(user, project_id)
    assert project.id is not None and user.id is not None
    settings = get_settings()
    if limit := settings.max_assistant_questions_per_user_per_day:
        day_start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
        asked = await AssistantQuestion.find(AssistantQuestion.user_id == user.id,
                                             AssistantQuestion.created_at >= day_start).count()
        if asked >= limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                f"You've asked today's {limit} questions; more are possible tomorrow (UTC).")
    kb = get_knowledge_base()
    if kb is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "The knowledge base (vector store) is unavailable. Check RAG_ENABLED and CHROMA_URL.")
    llm = get_provider()
    try:
        result = await ask(kb, llm, project.id, body.question.strip())
    except (LLMQuotaError, LLMRateLimitError) as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from None
    except LLMError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"The LLM could not answer: {exc}") from None
    finally:
        await llm.aclose()
    question = AssistantQuestion(
        project_id=project.id,
        user_id=user.id,
        question=body.question.strip(),
        answer=result.answer,
        sources=[AssistantSource(n=n, kind=hit.kind, ref_id=hit.ref_id, title=hit.title,
                                 run_id=hit.metadata.get("run_id"), similarity=hit.similarity,
                                 excerpt=hit.text[:EXCERPT_CHARS]) for n, hit in result.sources],
        trace=result.trace,
        llm_calls=result.llm_calls,
    )
    await question.insert()
    return answer_read(question)
