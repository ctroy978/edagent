"""LangGraph construction for multi-agent routing system."""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from edagent.state import AgentState
from edagent.nodes import (
    router_node,
    essay_grading_node,
    test_grading_node,
    general_node,
    route_decision,
)


def create_graph() -> StateGraph:
    """Create and compile the multi-agent routing graph.

    The graph follows this pattern:
    1. User input goes to Router
    2. Router analyzes intent and routes to appropriate expert
    3. Expert handles the request and returns response

    Returns:
        Compiled StateGraph ready for execution
    """
    # Create the graph
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("router", router_node)
    workflow.add_node("essay_grading", essay_grading_node)
    workflow.add_node("test_grading", test_grading_node)
    workflow.add_node("general", general_node)

    # Set entry point
    workflow.set_entry_point("router")

    # Add conditional routing from router to experts
    workflow.add_conditional_edges(
        "router",
        route_decision,
        {
            "essay_grading": "essay_grading",
            "test_grading": "test_grading",
            "general": "general",
        },
    )

    # All expert nodes end the graph
    workflow.add_edge("essay_grading", END)
    workflow.add_edge("test_grading", END)
    workflow.add_edge("general", END)

    # Compile the graph with memory checkpointing
    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)


# Create the global graph instance
graph = create_graph()
