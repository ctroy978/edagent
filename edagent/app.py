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
        # NOTE: Don't include job_id here - it persists from the checkpoint
        # next_step will be set by router, but we need to provide a value
        initial_state = {
            "messages": [HumanMessage(content=message_content)],
            "next_step": "",  # Router will set the actual value
        }

        # Config with thread_id for memory persistence
        config = {"configurable": {"thread_id": thread_id}}

        # Create a step to show processing activity
        async with cl.Step(name="Processing", type="run") as step:
            step.input = message_content

            # Stream through the graph with memory
            final_state = None
            current_node = None

            # Node display names for better UX
            node_names = {
                "router": "🔍 Understanding your request",
                "gather_materials": "📋 Gathering materials",
                "prepare_essays": "📄 Preparing essays for grading",
                "inspect_and_scrub": "🔍 Inspecting and scrubbing PII",
                "evaluate_essays": "✍️ Evaluating essays",
                "generate_reports": "📊 Generating reports",
                "test_grading": "📋 Processing test grading request",
                "general": "💬 Preparing response",
                "email_distribution": "📧 Preparing email distribution",
            }

            async for output in graph.astream(initial_state, config):
                # Update step with current node
                node_name = list(output.keys())[0]

                if node_name != current_node:
                    current_node = node_name
                    display_name = node_names.get(node_name, f"Processing {node_name}")
                    step.name = display_name
                    await step.update()

                final_state = output[node_name]

            # Extract final response
            elements = []
            if final_state and final_state.get("messages"):
                final_message = final_state["messages"][-1]
                response_content = final_message.content
                step.output = "✓ Complete"

                # Detect file paths in response to create download buttons
                import re
                from pathlib import Path

                # Enhanced pattern to match file paths with common extensions
                # Matches: /absolute/path/file.ext, data/relative/file.ext, C:\windows\path\file.ext
                path_patterns = [
                    r'(?:/[\w\-./]+/[\w\-]+\.(?:csv|zip|pdf))',  # Absolute Unix paths
                    r'(?:data/[\w\-./]+\.(?:csv|zip|pdf))',      # Relative paths starting with data/
                    r'(?:[A-Za-z]:[\\\/][\w\-\\\/]+\.(?:csv|zip|pdf))',  # Windows paths
                ]

                found_paths = []
                for pattern in path_patterns:
                    found_paths.extend(re.findall(pattern, response_content))

                seen_paths = set()
                for file_path in found_paths:
                    # Clean up path (remove trailing punctuation)
                    file_path = file_path.rstrip(".,;:)").strip()

                    # Convert to absolute path if relative
                    path_obj = Path(file_path)
                    if not path_obj.is_absolute():
                        # Resolve relative to current working directory
                        path_obj = Path.cwd() / path_obj

                    file_path_abs = str(path_obj)

                    # Deduplicate and check existence
                    if file_path_abs not in seen_paths and path_obj.exists():
                        seen_paths.add(file_path_abs)
                        try:
                            elements.append(
                                cl.File(
                                    name=path_obj.name,
                                    path=file_path_abs,
                                    display="inline"
                                )
                            )
                            print(f"✓ Attached file for download: {path_obj.name}")
                        except Exception as e:
                            print(f"✗ Error attaching file {file_path_abs}: {e}")
                    elif file_path_abs not in seen_paths:
                        print(f"⚠ File not found: {file_path_abs}")
            else:
                response_content = "I apologize, but I encountered an issue processing your request. Please try again."
                step.output = "✗ Error"

        # Send final response
        await cl.Message(content=response_content, elements=elements).send()

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[APP ERROR] {error_trace}", flush=True)

        error_message = f"I encountered an error: {type(e).__name__}: {str(e)}\n\nPlease make sure:\n1. Your .env file is configured correctly\n2. The MCP server path is valid\n3. You have the necessary API keys set\n\nError details have been logged to the console."
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
