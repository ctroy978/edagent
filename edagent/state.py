"""LangGraph State Schema for the multi-agent system."""

from typing import TypedDict, Annotated, Sequence
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """State for the multi-agent routing system.

    Attributes:
        messages: Conversation history (automatically merged by add_messages)
        next_step: Routing decision ('grading', 'curriculum', 'general', or END)
    """

    messages: Annotated[Sequence[BaseMessage], add_messages]
    next_step: str
