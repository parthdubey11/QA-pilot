"""RAG memory: QA history (bugs, test results, a11y issues, heals) embedded into ChromaDB via LangChain.

Used by the Reporter (similar past bugs for dedup and regressions), the Healer (earlier repairs), the bug page
("similar bugs") and the assistant (questions about a project's history, worker/agents/assistant.py).
"""

from app.rag.memory import (a11y_document, bug_document, forget_project, heal_document, remember,
                            test_document)
from app.rag.store import KINDS, Hit, KnowledgeBase, Kind, get_knowledge_base, set_knowledge_base

__all__ = ["KINDS", "Hit", "Kind", "KnowledgeBase", "a11y_document", "bug_document", "forget_project",
           "get_knowledge_base", "heal_document", "remember", "set_knowledge_base", "test_document"]
