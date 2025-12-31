"""LangGraph nodes for the multi-agent routing system."""

import os
import json
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_xai import ChatXAI
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from edagent.state import AgentState
from edagent.mcp_tools import get_grading_tools


# --- Router Decision Schema ---
class RouterDecision(BaseModel):
    """Structured output for routing decision."""

    reasoning: str = Field(description="Brief explanation of routing decision")
    next_step: Literal["essay_grading", "test_grading", "general"] = Field(
        description="Which expert to route to: essay_grading for written essays, test_grading for tests/quizzes, or general for other requests"
    )


# --- LLM Setup ---
def get_llm(with_structured_output: bool = False):
    """Get configured LLM instance.

    Args:
        with_structured_output: If True, return LLM configured for structured output

    Returns:
        Configured LLM instance
    """
    # Check which API key is available (prioritize xAI)
    if os.getenv("XAI_API_KEY"):
        model = os.getenv("XAI_MODEL", "grok-2-1212")
        llm = ChatXAI(model=model, temperature=0)
    elif os.getenv("OPENAI_API_KEY"):
        model = os.getenv("OPENAI_MODEL", "gpt-4-turbo-preview")
        llm = ChatOpenAI(model=model, temperature=0)
    elif os.getenv("ANTHROPIC_API_KEY"):
        model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        llm = ChatAnthropic(model=model, temperature=0)
    else:
        raise ValueError(
            "No API key found. Set XAI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY"
        )

    if with_structured_output:
        return llm.with_structured_output(RouterDecision)
    return llm


# --- Router Node (Concierge) ---
async def router_node(state: AgentState) -> AgentState:
    """Router/Concierge node that analyzes intent and routes to appropriate expert.

    This node acts as a friendly receptionist, understanding user needs and
    directing them to the right specialist.

    Args:
        state: Current agent state

    Returns:
        Updated state with routing decision
    """
    system_prompt = """You are a helpful educational assistant and concierge. Your role is to understand what the user needs and route them to the appropriate specialist.

The system has powerful OCR document processing tools for grading student work. You must determine WHAT TYPE of grading is needed:

**ESSAY GRADING (route to "essay_grading"):**
- Written essays with paragraph responses
- Long-form answers requiring detailed analysis
- Papers with extended written content
- Assignments needing qualitative feedback on writing quality, arguments, structure
- Keywords: essay, paper, writing assignment, composition, written response

**TEST GRADING (route to "test_grading"):**
- Multiple choice tests
- Short answer questions
- Fill-in-the-blank quizzes
- Bubble tests / scantron sheets
- True/false questions
- Tests with objective/factual answers
- Keywords: test, quiz, exam, multiple choice, short answer, bubble sheet

**GENERAL (route to "general"):**
- Questions about education
- Requests for curriculum/lesson plans (no special tools available)
- Creating handouts or materials
- Anything that doesn't involve grading student submissions

**Available MCP Tools (for both grading types):**
- batch_process_documents - Process directory of PDFs with OCR
- process_pdf_document - Process single PDF
- extract_text_from_image - OCR for images
- read_file - Read output files

**Decision Logic:**
1. Does request involve grading student work? → Yes, continue; No → general
2. Is it essays/papers with extended writing? → essay_grading
3. Is it tests/quizzes with short answers? → test_grading
4. Unclear or mentions both? → Ask user to clarify

IMPORTANT: Essay grading and test grading require different evaluation approaches. Route carefully based on the TYPE of assignment."""

    llm = get_llm(with_structured_output=True)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    decision = await llm.ainvoke(messages)

    # Create user-friendly routing message
    routing_messages = {
        "essay_grading": "I'd be happy to help you grade those essays! To make sure I give you the best results, let me ask you a few questions about the assignment first.",
        "test_grading": "I'll help you grade those tests! Let me ask you a few questions about the assignment to understand what you need.",
        "general": "I'm here to help with your question.",
    }

    return {
        "next_step": decision.next_step,
        "messages": [
            AIMessage(
                content=routing_messages.get(
                    decision.next_step, "Let me help you with that."
                )
            )
        ],
    }


# --- Essay Grading Expert Node ---
async def essay_grading_node(state: AgentState) -> AgentState:
    """Essay grading expert node specialized for written essays and papers.

    Args:
        state: Current agent state

    Returns:
        Updated state with essay grading response
    """
    system_prompt = """You are an essay grading assistant helping teachers grade student essays using a comprehensive AI-powered pipeline.

**CRITICAL RULES:**
1. NEVER show your internal thinking or reasoning to the user
2. Ask ONE question at a time - be conversational, not overwhelming
3. ALWAYS understand the context BEFORE asking for materials
4. Guide teachers step-by-step through the complete grading workflow

**THE COMPLETE GRADING PIPELINE:**
Your goal is to guide the teacher through this workflow:
1. UNDERSTAND THE ASSIGNMENT CONTEXT (Ask discovery questions)
2. Gather Required Materials (Essays + Rubric)
3. Gather Optional Materials (Test Question, Reading Materials, Lecture Notes)
4. Process Essays (OCR with batch_process_documents)
5. Human Inspection Checkpoint (get_job_statistics - verify student detection)
6. Privacy Protection (scrub_processed_job)
7. Optional: Knowledge Base Setup (add_to_knowledge_base + query_knowledge_base)
8. Evaluate Essays (evaluate_job with rubric and context)
9. Generate Reports (generate_gradebook + generate_student_feedback)
10. Deliver Results to Teacher

**PHASE 0: UNDERSTAND THE ASSIGNMENT CONTEXT (START HERE!)**

Before asking for any materials, understand what the teacher has. Ask these questions ONE AT A TIME:

**Q1: "Are the essays handwritten or typed?"**
- This determines OCR quality expectations
- Handwritten = may need normalization step
- Typed = usually cleaner OCR

**Q2: "Was there a specific question or essay prompt for this assignment?"**
- If YES: Ask them to provide it (crucial for evaluation context)
- If NO: Note that this is an open-topic assignment

**Q3: "Did students use specific reading materials or sources for these essays?"**
- Examples: textbook chapters, articles, lecture notes
- If YES: These will be added to knowledge base for context-aware grading
- If NO: Grading will be rubric-based only

**Q4: "How many students' essays are you grading?"**
- This sets expectations for processing time
- Helps verify student detection later

ONLY AFTER understanding the context should you ask for materials.

**PHASE 1: GATHER REQUIRED MATERIALS**

**Essays (REQUIRED):**
- Teachers can upload essays using the 📎 attachment button
- Accept: Multiple PDF files OR a ZIP file containing PDFs
- If they upload a ZIP, explain you'll extract it to a temporary folder
- If they upload PDFs directly, explain you'll organize them for processing
- NEVER ask for "directory paths" - only work with uploaded files

**Rubric (REQUIRED):**
- Can be uploaded as a file (PDF, TXT, DOCX) OR typed directly in chat
- Ask: "Do you have a grading rubric? You can upload it with 📎 or paste it here."

**PHASE 2: GATHER OPTIONAL MATERIALS (Based on Context from Phase 0)**

If they mentioned reading materials in Phase 0, ask for them now:
- "Can you upload the reading materials the students used? (textbook chapters, articles, etc.)"
- If they mentioned lecture notes, ask for those too

If they provided a test question in Phase 0, you already have it.

**PHASE 3: FILE HANDLING**

When files are attached, you'll see: "[User attached files: /path1, /path2...]"
- If it's a ZIP: Extract to a temporary directory for processing
- If it's PDFs: Note their paths for batch_process_documents
- If it's reading materials: Note for add_to_knowledge_base

**PHASE 4: EXECUTE PIPELINE**

Once you have materials, execute these steps IN ORDER:

**Step 1: OCR Processing**
- Call: batch_process_documents(directory_path=<pdf_folder>, job_name=<descriptive_name>)
- This returns a job_id
- Explain: "I'm processing the essays with OCR. This detects student names and extracts text..."

**Step 2: Inspection Checkpoint**
- Call: get_job_statistics(job_id=<job_id>)
- Show teacher the manifest: "I found X students. Here's the list with page counts..."
- Ask: "Does this look correct? Are all your students accounted for?"
- If NO: Explain name detection requirements and offer to retry
- If YES: Proceed to scrubbing

**Step 3: Privacy Protection**
- Call: scrub_processed_job(job_id=<job_id>)
- Explain: "Removing student names for privacy before AI evaluation..."

**Step 4: Knowledge Base (If Optional Materials Provided)**
- If reading materials provided:
  - Call: add_to_knowledge_base(file_paths=[...], topic=<descriptive_topic>)
  - Call: query_knowledge_base(query=<derive from test question/rubric>, topic=<topic>)
  - Use retrieved context in evaluation

**Step 5: Evaluation**
- Call: evaluate_job(job_id=<job_id>, rubric=<rubric_text>, context_material=<from_KB_or_empty>, system_instructions=<test_question_if_provided>)
- Explain: "Grading essays against your rubric..."

**Step 6: Generate Reports**
- Call: generate_gradebook(job_id=<job_id>)
- Call: generate_student_feedback(job_id=<job_id>)
- Announce: "Your gradebook CSV and individual student feedback PDFs are ready!"
- Provide file paths for download

**IMPORTANT NOTES:**
- Student names must appear as "Name: John Doe" at TOP of FIRST PAGE only
- Missing names → labeled "Unknown Student 01" (mention this during inspection)
- Be encouraging and patient - teachers may not be tech-savvy
- Celebrate progress at each step: "Great! Essays processed. Now let's verify..."

**TOOLS AVAILABLE:**
- batch_process_documents, get_job_statistics, scrub_processed_job
- evaluate_job, generate_gradebook, generate_student_feedback
- add_to_knowledge_base, query_knowledge_base
- Plus file utilities for handling uploads

Always be supportive, clear, and guide the teacher to successful grading results."""

    # Get grading-specific tools from MCP server
    tools = await get_grading_tools()

    # Add file handling utilities
    from edagent.file_utils import (
        extract_zip_to_temp,
        organize_pdfs_to_temp,
        read_text_file,
        list_directory_files,
    )

    # Add all utilities to tools
    tools = tools + [
        extract_zip_to_temp,
        organize_pdfs_to_temp,
        read_text_file,
        list_directory_files,
    ]

    llm = get_llm().bind_tools(tools)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # Agentic loop: Allow multiple rounds of tool calling
    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        response = await llm.ainvoke(messages)
        messages.append(response)

        # Check if there are tool calls to execute
        if not response.tool_calls:
            # No more tool calls, we're done
            break

        # Execute all tool calls
        from langchain_core.messages import ToolMessage

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            # Find and execute the tool
            matching_tool = next((t for t in tools if t.name == tool_name), None)
            if matching_tool:
                try:
                    result = await matching_tool.ainvoke(tool_args)
                    # Add tool result to messages
                    messages.append(
                        ToolMessage(
                            content=str(result),
                            tool_call_id=tool_call["id"],
                            name=tool_name,
                        )
                    )
                except Exception as e:
                    # Add error message
                    messages.append(
                        ToolMessage(
                            content=f"Error executing {tool_name}: {str(e)}",
                            tool_call_id=tool_call["id"],
                            name=tool_name,
                        )
                    )
            else:
                messages.append(
                    ToolMessage(
                        content=f"Tool {tool_name} not found",
                        tool_call_id=tool_call["id"],
                        name=tool_name,
                    )
                )

        iteration += 1

    # Return the final state with all messages
    return {"next_step": "END", "messages": messages[len(state["messages"]) :]}


# --- Test Grading Expert Node ---
async def test_grading_node(state: AgentState) -> AgentState:
    """Test grading expert node specialized for tests, quizzes, and short answers.

    Args:
        state: Current agent state

    Returns:
        Updated state with test grading response
    """
    system_prompt = """You are an expert in grading TESTS, QUIZZES, and SHORT-ANSWER assignments. You specialize in:
- Multiple choice questions
- True/false questions
- Short answer responses (1-2 sentences)
- Fill-in-the-blank
- Objective, factual answers
- Answer key matching
- Bubble sheet processing

You have access to OCR and document processing tools (same as essay grading).

**Grading Approach for Tests:**
1. Extract text from test PDFs
2. Identify questions and student answers
3. Compare against answer key (if provided)
4. Award points based on correctness
5. Provide brief feedback on incorrect answers
6. Calculate total score

Your grading is more objective and score-focused than essay grading. You check for correctness, not writing quality.

Always be fair and consistent in applying answer keys."""

    # Get grading-specific tools
    tools = await get_grading_tools()

    # Add file reading tool
    from langchain_core.tools import tool as tool_decorator

    @tool_decorator
    def read_file(file_path: str) -> str:
        """Read contents of a file.

        Args:
            file_path: Path to the file to read

        Returns:
            Contents of the file as a string
        """
        try:
            with open(file_path, "r") as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"

    tools = tools + [read_file]

    llm = get_llm().bind_tools(tools)
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # Agentic loop (same as essay grading)
    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        response = await llm.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        from langchain_core.messages import ToolMessage

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            matching_tool = next((t for t in tools if t.name == tool_name), None)
            if matching_tool:
                try:
                    result = await matching_tool.ainvoke(tool_args)
                    messages.append(
                        ToolMessage(
                            content=str(result),
                            tool_call_id=tool_call["id"],
                            name=tool_name,
                        )
                    )
                except Exception as e:
                    messages.append(
                        ToolMessage(
                            content=f"Error executing {tool_name}: {str(e)}",
                            tool_call_id=tool_call["id"],
                            name=tool_name,
                        )
                    )
            else:
                messages.append(
                    ToolMessage(
                        content=f"Tool {tool_name} not found",
                        tool_call_id=tool_call["id"],
                        name=tool_name,
                    )
                )

        iteration += 1

    return {"next_step": "END", "messages": messages[len(state["messages"]) :]}


# --- Curriculum Expert Node ---
async def curriculum_node(state: AgentState) -> AgentState:
    """Curriculum design expert node.

    Args:
        state: Current agent state

    Returns:
        Updated state with curriculum response
    """
    system_prompt = """You are an expert in curriculum design and educational content creation. You specialize in:
- Designing comprehensive lesson plans
- Creating learning objectives aligned with educational standards
- Structuring courses and units
- Suggesting engaging activities and assessments
- Adapting content for different learning levels

When helping users design curriculum:
1. Understand the subject matter and target audience
2. Clarify learning objectives and outcomes
3. Suggest a logical progression of topics
4. Include varied assessment methods
5. Consider diverse learning styles

Be creative, practical, and evidence-based in your recommendations."""

    llm = get_llm()

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    response = await llm.ainvoke(messages)

    return {"next_step": "END", "messages": [response]}


# --- General Chat Node ---
async def general_node(state: AgentState) -> AgentState:
    """General chat node for questions that don't require specialized routing.

    Args:
        state: Current agent state

    Returns:
        Updated state with general response
    """
    system_prompt = """You are a helpful educational assistant. You can answer general questions about:
- Educational concepts and theories
- Teaching strategies and best practices
- Technology in education
- General guidance and clarification

If a user's request becomes more specific and should be handled by a specialist (grading or curriculum), 
politely suggest they ask a more specific question so you can route them appropriately."""

    llm = get_llm()

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    response = await llm.ainvoke(messages)

    return {"next_step": "END", "messages": [response]}


# --- Routing Logic ---
def route_decision(
    state: AgentState,
) -> Literal["essay_grading", "test_grading", "general"]:
    """Conditional edge function that determines which expert to route to.

    Args:
        state: Current agent state

    Returns:
        Name of the next node to execute
    """
    return state["next_step"]
