"""Chainlit UI application for the multi-agent educational assistant."""

import os
from dotenv import load_dotenv
import chainlit as cl
from langchain_core.messages import HumanMessage

from edagent.graph import graph

# Load environment variables
load_dotenv()


@cl.on_chat_start
async def on_chat_start():
    """Initialize chat session with welcome message and quick-start options."""

    welcome_message = """# Welcome to EdAgent! 🎓

I'm your AI teaching assistant with powerful document processing capabilities.

**What I Can Help You With:**

📝 **Grade Student Essays**
- Process handwritten or typed essays
- Provide detailed qualitative feedback
- Evaluate writing quality, structure, and arguments

📋 **Grade Tests & Quizzes** *(Coming Soon)*
- Score multiple choice questions
- Grade short answer responses
- Process bubble sheets

💬 **General Teaching Support**
- Answer questions about education
- Provide guidance on teaching strategies

---

**How to Get Started:**

Simply tell me what you need! For example:
- "I have essays to grade"
- "I need to grade student tests"
- "Can you help me with [teaching question]?"

I'll guide you through the specific requirements for each task.

**What would you like help with today?**
"""

    await cl.Message(content=welcome_message).send()

    # Set up quick-start actions
    starters = [
        cl.Starter(
            label="📝 Grade Essays",
            message="I have student essays to grade",
            icon="/public/grade.svg",
        ),
        cl.Starter(
            label="📋 Grade Tests",
            message="I have tests or quizzes to grade",
            icon="/public/test.svg",
        ),
        cl.Starter(
            label="💬 Ask a Question",
            message="I have a general question about teaching",
            icon="/public/question.svg",
        ),
        cl.Starter(
            label="❓ How Does This Work?",
            message="Can you explain how the grading system works and what I need to prepare?",
            icon="/public/info.svg",
        ),
    ]

    # Store starters in user session
    cl.user_session.set("starters", starters)


@cl.on_message
async def on_message(message: cl.Message):
    """Handle incoming user messages by routing through the LangGraph agent.

    Args:
        message: The user's message
    """
    # Create a thinking message to show activity
    thinking_msg = cl.Message(content="")
    await thinking_msg.send()

    try:
        # Get or create a unique thread_id for this user session
        thread_id = cl.user_session.get("thread_id")
        if not thread_id:
            import uuid

            thread_id = str(uuid.uuid4())
            cl.user_session.set("thread_id", thread_id)

        # Check for attached files
        attached_files_info = ""
        if message.elements:
            file_paths = []
            for element in message.elements:
                if hasattr(element, "path"):
                    file_paths.append(element.path)
            if file_paths:
                attached_files_info = (
                    f"\n\n[User attached files: {', '.join(file_paths)}]"
                )

        # Prepare message content with file info if present
        message_content = message.content + attached_files_info

        # Prepare initial state with just the new message
        # LangGraph memory will handle conversation history automatically
        initial_state = {
            "messages": [HumanMessage(content=message_content)],
            "next_step": "",
        }

        # Config with thread_id for memory persistence
        config = {"configurable": {"thread_id": thread_id}}

        # Stream through the graph with memory
        final_state = None
        async for output in graph.astream(initial_state, config):
            # Update thinking message with progress
            node_name = list(output.keys())[0]
            await thinking_msg.update()

            final_state = output[node_name]

        # Extract final response
        if final_state and final_state.get("messages"):
            final_message = final_state["messages"][-1]
            response_content = final_message.content
        else:
            response_content = "I apologize, but I encountered an issue processing your request. Please try again."

        # Send final response - remove the thinking message and send a new one
        await thinking_msg.remove()
        await cl.Message(content=response_content).send()

    except Exception as e:
        error_message = f"I encountered an error: {str(e)}\n\nPlease make sure:\n1. Your .env file is configured correctly\n2. The MCP server path is valid\n3. You have the necessary API keys set"
        await thinking_msg.remove()
        await cl.Message(content=error_message).send()


@cl.set_starters
async def set_starters():
    """Define quick-start buttons for the chat interface."""
    return [
        cl.Starter(
            label="📝 Grade Essays",
            message="I have student essays to grade",
        ),
        cl.Starter(
            label="📋 Grade Tests",
            message="I have tests or quizzes to grade",
        ),
        cl.Starter(
            label="💬 Ask a Question",
            message="I have a general question about teaching",
        ),
        cl.Starter(
            label="❓ How Does This Work?",
            message="Can you explain how the grading system works and what I need to prepare?",
        ),
    ]


if __name__ == "__main__":
    # This allows running with: uv run python -m edagent.app
    from chainlit.cli import run_chainlit

    run_chainlit(__file__)
