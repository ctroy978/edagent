"""LangGraph construction for multi-agent routing system."""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from edagent.state import AgentState
from edagent.nodes import (
    router_node,
    essay_grading_node,
    test_grading_node,
    general_node,
    email_distribution_node,
    route_decision,
)


def create_graph() -> StateGraph:
    """Create and compile the multi-agent routing graph.

    The graph follows this pattern:
    1. User input goes to Router
    2. Router analyzes intent and routes to appropriate expert
    3. Expert handles the request
    4. Grading experts can optionally route to email distribution
    5. Email distribution completes and ends workflow

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
    workflow.add_node("email_distribution", email_distribution_node)

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

    # Grading nodes can route to email distribution or end
    workflow.add_conditional_edges(
        "essay_grading",
        route_decision,
        {
            "email_distribution": "email_distribution",
            "END": END,
        },
    )

    workflow.add_conditional_edges(
        "test_grading",
        route_decision,
        {
            "email_distribution": "email_distribution",
            "END": END,
        },
    )

    # General node always ends
    workflow.add_edge("general", END)

    # Email distribution always ends after completing
    workflow.add_edge("email_distribution", END)

    # Compile the graph with memory checkpointing
    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)


# Create the global graph instance
graph = create_graph()
