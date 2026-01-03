"""LangGraph State Schema for the multi-agent system."""

from typing import TypedDict, Annotated, Sequence
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """State for the multi-agent routing system.

    Attributes:
        messages: Conversation history (automatically merged by add_messages)
        next_step: Routing decision ('essay_grading', 'test_grading', 'general', 'email_distribution', or END)
        job_id: Optional job ID for grading operations (passed to email distribution)
    """

    messages: Annotated[Sequence[BaseMessage], add_messages]
    next_step: str
    job_id: str | None
