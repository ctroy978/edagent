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
    next_step: Literal["gather_materials", "test_grading", "general", "email_distribution"] = Field(
        description="Which expert to route to: gather_materials for written essays (starts essay grading workflow), test_grading for tests/quizzes, email_distribution for sending graded feedback, or general for other requests"
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
    # Check if there's a pending job_id and user is asking about email
    from langchain_core.messages import AIMessage

    job_id = state.get("job_id")
    current_phase = state.get("current_phase")
    last_message = state["messages"][-1].content.lower() if state["messages"] else ""

    # DEBUG: Print state info
    print(f"[ROUTER DEBUG] job_id in state: {job_id}")
    print(f"[ROUTER DEBUG] current_phase in state: {current_phase}")
    print(f"[ROUTER DEBUG] last_message: {last_message}")
    print(f"[ROUTER DEBUG] full state keys: {state.keys()}")

    # Check if we're continuing a workflow (phase in progress)
    phase_routing = {
        "gather": "gather_materials",
        "prepare": "prepare_essays",
        "inspect": "inspect_and_scrub",
        "evaluate": "evaluate_essays",
        "report": "generate_reports",
    }

    if current_phase and current_phase in phase_routing:
        # Continue the workflow at the current phase
        next_node = phase_routing[current_phase]
        print(f"[ROUTER DEBUG] Continuing workflow at phase: {current_phase} → {next_node}")
        return {
            "next_step": next_node,
            "messages": [],  # Don't add routing message, let the phase node handle it
        }

    # Keywords that indicate email intent
    email_keywords = ["email", "send", "distribute", "mail", "yes", "yeah", "yep", "sure", "ok", "okay"]

    if job_id and any(keyword in last_message for keyword in email_keywords):
        # User has a completed grading job and is confirming email distribution
        print(f"[ROUTER DEBUG] Routing to email_distribution with job_id: {job_id}")
        return {
            "next_step": "email_distribution",
            "job_id": job_id,  # CRITICAL: Pass through the job_id
            "messages": [AIMessage(content=f"Great! Let me help you distribute these via email. (Using job_id: {job_id})")],
        }

    print(f"[ROUTER DEBUG] Not routing to email - proceeding to LLM decision")

    system_prompt = """You are a helpful educational assistant and concierge. Your role is to understand what the user needs and route them to the appropriate specialist.

The system has powerful OCR document processing tools for grading student work. You must determine WHAT TYPE of grading is needed:

**ESSAY GRADING (route to "gather_materials"):**
- Written essays with paragraph responses
- Long-form answers requiring detailed analysis
- Papers with extended written content
- Assignments needing qualitative feedback on writing quality, arguments, structure
- Keywords: essay, paper, writing assignment, composition, written response
- This starts the 5-phase essay grading workflow

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
- extract_text_from_image - OCR for images
- convert_pdf_to_text - Convert PDFs (including scanned) to text
- convert_image_to_pdf - Convert images to PDF
- read_file - Read output files

**Decision Logic:**
1. Does request involve grading student work? → Yes, continue; No → general
2. Is it essays/papers with extended writing? → gather_materials
3. Is it tests/quizzes with short answers? → test_grading
4. Unclear or mentions both? → Ask user to clarify

IMPORTANT: Essay grading and test grading require different evaluation approaches. Route carefully based on the TYPE of assignment."""

    llm = get_llm(with_structured_output=True)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    decision = await llm.ainvoke(messages)

    # Create user-friendly routing message
    routing_messages = {
        "gather_materials": "I'd be happy to help you grade those essays!",
        "test_grading": "I'll help you grade those tests!",
        "general": "I'm here to help with your question.",
        "email_distribution": "Great! Let me help you distribute these via email.",
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


# --- NEW: Gather Materials Node (Phase 0 & 1) ---
async def gather_materials_node(state: AgentState) -> AgentState:
    """Gather materials node - collects rubric, question, reading materials, and metadata.

    This is the first node in the essay grading workflow. It presents an overview
    (on first message only) and then collects all necessary materials one at a time:
    - Rubric (required)
    - Essay question/prompt (optional)
    - Reading materials (optional)
    - Format (handwritten/typed) and student count

    Args:
        state: Current agent state

    Returns:
        Updated state with gathered materials and next_step="prepare_essays"
    """
    system_prompt = """You are an essay grading assistant helping teachers gather the materials needed for grading.

**YOUR ROLE:** Collect all necessary materials before processing begins.

**CRITICAL RULES:**
1. Ask ONE question at a time - be conversational, not overwhelming
2. NEVER show internal thinking or reasoning to the user
3. Present overview ONLY on first message (check if current_phase is None)
4. NEVER repeat greetings or overviews - stay contextual

**PHASE 0: PRESENT OVERVIEW (ONLY IF current_phase IS None)**

If this is the FIRST message (current_phase is None), present this overview:

"Great! I'd love to help you grade those essays. Here's how this works—there are a few things I might need to give you the best grading results:

**Required:**
- **A grading rubric** - This tells me what criteria to use when evaluating the essays
- **The student essays** - I'll use OCR to extract the text, so handwritten or typed both work
- **Format info** - Whether they're handwritten or typed helps me optimize the text extraction

**Optional (but helpful for better grading):**
- **The essay question or prompt** - If students were answering a specific question, this gives me important context
- **Reading materials or lecture notes** - If students were supposed to reference specific sources, I can use them to check accuracy and provide context-aware grading

**Supported file formats:** PDF, images (JPG/PNG), ZIP files
**Note:** Google Docs shortcuts (.gdoc) and Word documents (.docx) are not supported—please convert/export to PDF first.

You don't have to give me all this at once! Let me help you upload what you need, one piece at a time.

Let's start: Do you have a grading rubric? You can upload it with 📎 or paste it directly here."

**PHASE 1: GATHER MATERIALS ONE AT A TIME**

**Step 1: Rubric (REQUIRED)**
- If overview was just presented, wait for rubric upload/paste
- If they upload a PDF file: Call convert_pdf_to_text(file_path=<path>) to read it
- If they paste text: Store it directly
- When received, acknowledge briefly and move to Step 2

**Step 2: Essay Question/Prompt (OPTIONAL)**
- Ask: "Perfect! Was there a specific essay question or prompt the students had to answer? If so, you can share it here or upload it with 📎"
- If YES and they upload PDF: Call convert_pdf_to_text(file_path=<path>)
- If YES and they paste: Store it
- If NO: "No problem! Moving on..."
- Move to Step 3

**Step 3: Reading Materials (OPTIONAL)**
- Ask: "Did students use any specific reading materials, textbook chapters, articles, or lecture notes for these essays?"
- If YES: "Great! Can you upload those materials now? I'll add them to my knowledge base to provide context-aware grading. Just use the 📎 button."
- **WAIT for upload - DO NOT proceed until you have the files**
- **NEVER search online** - only use what teacher uploads
- When received: Store the file paths (you'll process them in the next phase)
- If NO: "No problem! I'll grade based on the rubric alone."
- Move to Step 4

**Step 4: Format & Student Count (REQUIRED)**
- Ask: "Are the essays handwritten or typed? This helps me optimize text extraction."
- Store the answer
- Then ask: "How many student essays are you grading?"
- Store the number
- Move to completion

**TOOLS AVAILABLE:**
- convert_pdf_to_text: Read PDF files (rubrics, questions, etc.)
- convert_image_to_pdf: Convert images to PDF format

**FILE HANDLING:**
When files are attached, you'll see: "[User attached files: /path1, /path2...]"
- For rubric/question PDFs: Use convert_pdf_to_text(file_path=<path>) to read them
- For rubric/question images: These will be handled by convert_pdf_to_text as well
- For reading materials: Just store the paths - you'll process them in prepare_essays phase
- For essays: Wait until Step 4 is complete, then signal completion
- **CRITICAL**: ALL file reading MUST use MCP tools - this is the core purpose of the agent

**STATE UPDATES:**
As you collect materials, update the state:
- rubric_text: Store rubric content
- question_text: Store question content (or None)
- reading_materials_paths: Store list of reading material paths (or empty list)
- essay_format: Store "handwritten" or "typed"
- student_count: Store expected number of students

**EXIT CONDITION:**
Once you have:
- ✓ Rubric
- ✓ Question (or confirmed not needed)
- ✓ Reading materials (or confirmed not needed)
- ✓ Essay format
- ✓ Student count

**CRITICAL:** Call the `complete_material_gathering` tool with ALL gathered materials:
```
complete_material_gathering(
    rubric_text="<the rubric text>",
    question_text="<the question>" or None,
    reading_materials_paths=["<path1>", "<path2>"] or None,
    essay_format="handwritten" or "typed",
    student_count=<number>
)
```

This signals that gathering is complete and you're ready to move to essay preparation.

**IMPORTANT:**
- Be encouraging and patient
- Celebrate progress: "Great! Got the rubric. Now let's talk about..."
- Keep responses SHORT and CONTEXTUAL
- NEVER repeat the overview after the first message
- ALWAYS call complete_material_gathering when all materials are collected"""

    # Get MCP tools for file conversion and processing
    from edagent.mcp_tools import get_grading_tools
    from langchain_core.tools import tool as tool_decorator

    # Add a tool for the agent to signal completion and store gathered materials
    @tool_decorator
    def complete_material_gathering(
        rubric_text: str,
        question_text: str | None,
        reading_materials_paths: list[str] | None,
        essay_format: str,
        student_count: int,
    ) -> str:
        """Signal that all materials have been gathered and store them in state.

        Args:
            rubric_text: The grading rubric text
            question_text: The essay question/prompt (or None if not provided)
            reading_materials_paths: List of paths to reading materials (or None/empty if not provided)
            essay_format: Either "handwritten" or "typed"
            student_count: Expected number of students

        Returns:
            Confirmation message
        """
        gathered_state["rubric_text"] = rubric_text
        gathered_state["question_text"] = question_text
        gathered_state["reading_materials_paths"] = reading_materials_paths or []
        gathered_state["essay_format"] = essay_format
        gathered_state["student_count"] = student_count
        gathered_state["materials_complete"] = True  # CRITICAL: Signal completion!
        return f"✓ Materials gathered: Rubric, {student_count} {essay_format} essays. Ready to proceed to essay preparation."

    # Get MCP tools and add completion tool
    tools = await get_grading_tools()
    tools.append(complete_material_gathering)

    llm = get_llm().bind_tools(tools)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # Agentic loop: Allow multiple rounds of gathering
    max_iterations = 10
    iteration = 0

    # Track what we've gathered
    gathered_state = {
        "rubric_text": state.get("rubric_text"),
        "question_text": state.get("question_text"),
        "reading_materials_paths": state.get("reading_materials_paths") or [],
        "essay_format": state.get("essay_format"),
        "student_count": state.get("student_count"),
    }

    while iteration < max_iterations:
        response = await llm.ainvoke(messages)
        messages.append(response)

        # Check if there are tool calls to execute
        if not response.tool_calls:
            # No more tool calls - check if we're done gathering
            # Parse the latest AI message to see if materials have been gathered from conversation
            # For now, we'll rely on the agent to signal completion
            break

        # Execute all tool calls
        from langchain_core.messages import ToolMessage

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            print(f"[GATHER_MATERIALS] Iteration {iteration}: Calling tool '{tool_name}' with args: {tool_args}", flush=True)

            # Find and execute the tool
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

    # Return state - route to next phase if complete, otherwise stay in this node
    if gathered_state.get("materials_complete", False):
        return {
            "current_phase": "prepare",
            "next_step": "prepare_essays",
            "messages": messages[len(state["messages"]) :],
            **gathered_state,
        }
    else:
        # Not done yet - end this turn and wait for next user message
        # Router will route back here based on current_phase
        return {
            "next_step": "END",
            "current_phase": "gather",  # Remember where we are
            "messages": messages[len(state["messages"]) :],
            **{k: v for k, v in gathered_state.items() if v is not None},  # Only update non-None values
        }


# --- NEW: Prepare Essays Node (Phase 1 continued + 2 + 3) ---
async def prepare_essays_node(state: AgentState) -> AgentState:
    """Prepare essays node - handles file uploads, knowledge base, and OCR processing.

    This node:
    1. Asks user to upload essays
    2. Prepares files (handles ZIP, images, PDFs)
    3. Adds reading materials to knowledge base (if provided)
    4. Runs OCR via batch_process_documents
    5. Returns job_id and moves to inspection phase

    Args:
        state: Current agent state with gathered materials

    Returns:
        Updated state with job_id and OCR completion status
    """
    system_prompt = """You are an essay preparation coordinator. Your ONLY job is to call MCP server tools - you do NOT process essays yourself.

**CRITICAL: YOU ARE A COORDINATOR, NOT A PROCESSOR**
- The MCP server does ALL file processing and OCR - you just coordinate
- NEVER attempt to read, extract, or process essay content yourself
- Your job: Call the MCP tools to prepare and process files

**CONTEXT FROM PREVIOUS PHASE:**
You already have from the teacher:
- Rubric text: {rubric_status}
- Essay question: {question_status}
- Reading materials: {reading_materials_status}
- Essay format: {essay_format}
- Expected student count: {student_count}

**YOUR TASKS (IN ORDER):**

**Task 1: Ask for Essay Upload**
- Use format-appropriate guidance based on essay_format:
  - **Handwritten**: "Perfect! Now I'm ready for the student essays. Please upload them using 📎. Handwritten essays are typically scanned as one multi-page PDF. I accept PDFs, Images (JPG/PNG), or ZIP files. I'll convert images automatically!"
  - **Typed**: "Perfect! Now I'm ready for the student essays. Please upload them using 📎. For typed essays, you can upload individual PDFs or a ZIP file containing all essays. I accept PDFs, Images (JPG/PNG), or ZIP files."
- **IMPORTANT**: Mention that Google Docs (.gdoc) and Word documents (.docx) are NOT supported - they must export/download as PDF first

**Task 2: Handle File Uploads**
When files are attached, you'll see: "[User attached files: /path1, /path2...]"

- Call: prepare_files_for_grading(file_paths=[...])
  - This handles PDFs, ZIPs (auto-extracts), images (converts to PDF)
  - Returns: {{"directory_path": "/tmp/...", "warnings": [...]}}
- **CRITICAL - CHECK WARNINGS**:
  - Parse the JSON response
  - If warnings list is NOT empty, report to user immediately:
    - "I couldn't process these files: [list warnings]"
    - Explain how to fix (export Google Docs as PDF, etc.)
    - Ask if they want to upload corrected files or proceed
  - Only continue if warnings are empty OR user confirms to proceed
- Store the directory_path for OCR

**Task 3: Add Reading Materials to Knowledge Base (CONDITIONAL)**
- If reading_materials_paths is NOT empty:
  - Create a topic name from the question text (if available) or use "general_essays"
  - Example: question_text="Analyze Frost poetry" → topic="frost_poetry_essays"
  - Example: no question → topic="general_essays"
  - Call: add_to_knowledge_base(file_paths=reading_materials_paths, topic=<derived_topic>)
  - Confirm: "Great! I've added the reading materials to my knowledge base for context-aware grading."
  - Set materials_added_to_kb = True
- If reading_materials_paths IS empty:
  - Skip this step
  - Set materials_added_to_kb = False

**Task 4: Run OCR Processing**
- Create a descriptive job_name from available context:
  - If question_text exists: derive from question (e.g., "Frost_Poetry_Analysis")
  - Otherwise: use format like "Essays_<current_date>" (e.g., "Essays_20260105")
  - Keep it short, alphanumeric with underscores only
- Call: batch_process_documents(directory_path=<clean_pdf_directory_from_prepare_files>, job_name=<created_job_name>)
  - Use the directory_path from prepare_files_for_grading
  - **CRITICAL**: Do NOT pass the dpi parameter - omit it entirely
  - Returns: {{"job_id": "job_...", "total_documents": X, "students_detected": Y, "summary": {{...}}}}
- Explain results based on the summary:
  - If mostly Fast Text Extraction: "Great! Processed your typed essays using fast text extraction. Found X student records..."
  - If mostly OCR: "Processed your essays using OCR (scanned documents). Found X student records..."
  - If Mixed: "Processed X files: Y via fast extraction, Z via OCR. Found Total student records..."
- Store the job_id for next phase

**Task 5: Signal Completion**
- Call: complete_preparation(job_id=<job_id>, clean_directory_path=<path>, materials_added_to_kb=<bool>)
- This signals you're ready to move to inspection phase

**CRITICAL ERROR HANDLING:**
If ANY tool fails:
1. STOP immediately
2. Report error clearly: "I encountered an error while [action]: [error message]"
3. Suggest fixes:
   - File issues: "Try re-uploading or checking file format"
   - OCR issues: "Files might be corrupted or password-protected"
4. DO NOT continue to next task
5. All grading MUST go through MCP server - no workarounds

**MCP TOOL PARAMETER RULES:**
- NEVER pass null/None for optional parameters
- If optional and you want default, OMIT the key entirely
- Example CORRECT: batch_process_documents(directory_path="/tmp", job_name="Test")
- Example WRONG: batch_process_documents(directory_path="/tmp", job_name="Test", dpi=null)

**TOOLS AVAILABLE:**
- prepare_files_for_grading(file_paths)
- add_to_knowledge_base(file_paths, topic)
- batch_process_documents(directory_path, job_name)
- complete_preparation (signals completion)

Always be encouraging: "Great work! Essays are processed. Let's verify the student list next..."
"""

    # Prepare context-aware prompt
    rubric_status = "✓ Provided" if state.get("rubric_text") else "❌ Missing"
    question_status = state.get("question_text") or "Not provided (optional)"
    reading_materials_status = (
        f"{len(state.get('reading_materials_paths', []))} files"
        if state.get("reading_materials_paths")
        else "None (optional)"
    )
    essay_format = state.get("essay_format") or "Unknown"
    student_count = state.get("student_count") or "Unknown"

    system_prompt = system_prompt.format(
        rubric_status=rubric_status,
        question_status=question_status,
        reading_materials_status=reading_materials_status,
        essay_format=essay_format,
        student_count=student_count,
    )

    # Get MCP tools
    from edagent.mcp_tools import get_grading_tools
    from edagent.file_utils import prepare_files_for_grading
    from langchain_core.tools import tool as tool_decorator

    tools = await get_grading_tools()

    # Add file preparation utility
    tools.append(prepare_files_for_grading)

    # Add completion signal tool
    preparation_state = {
        "job_id": None,
        "clean_directory_path": None,
        "materials_added_to_kb": False,
        "ocr_complete": False,
    }

    @tool_decorator
    def complete_preparation(
        job_id: str, clean_directory_path: str, materials_added_to_kb: bool
    ) -> str:
        """Signal that preparation is complete and store results.

        Args:
            job_id: The job ID from batch_process_documents
            clean_directory_path: Path to prepared files directory
            materials_added_to_kb: Whether reading materials were added to KB

        Returns:
            Confirmation message
        """
        preparation_state["job_id"] = job_id
        preparation_state["clean_directory_path"] = clean_directory_path
        preparation_state["materials_added_to_kb"] = materials_added_to_kb
        preparation_state["ocr_complete"] = True
        return f"✓ Preparation complete. Job ID: {job_id}. Ready to inspect student list."

    tools.append(complete_preparation)

    llm = get_llm().bind_tools(tools)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # Agentic loop
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

            print(
                f"[PREPARE_ESSAYS] Iteration {iteration}: Calling tool '{tool_name}' with args: {tool_args}",
                flush=True,
            )

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

    # Return state - route to next phase if complete, otherwise stay in this node
    if preparation_state.get("ocr_complete", False):
        return {
            "current_phase": "inspect",
            "next_step": "inspect_and_scrub",
            "job_id": preparation_state["job_id"],
            "clean_directory_path": preparation_state["clean_directory_path"],
            "materials_added_to_kb": preparation_state["materials_added_to_kb"],
            "ocr_complete": preparation_state["ocr_complete"],
            "messages": messages[len(state["messages"]) :],
        }
    else:
        # Not done yet - route back to this node
        return {
            "next_step": "prepare_essays",
            "messages": messages[len(state["messages"]) :],
            # Preserve any state that was set
            **{k: v for k, v in preparation_state.items() if v not in (None, False)},
        }


# --- NEW: Inspect and Scrub Node (Phase 4 + 5) ---
async def inspect_and_scrub_node(state: AgentState) -> AgentState:
    """Inspection and privacy protection node - verifies student detection and scrubs PII.

    This node:
    1. Calls get_job_statistics to retrieve student manifest
    2. Shows manifest to teacher for verification
    3. Asks teacher: "Does this look correct?"
    4. If approved, calls scrub_processed_job to remove PII
    5. Moves to evaluation phase

    Args:
        state: Current agent state with job_id from OCR

    Returns:
        Updated state with scrubbing completion status
    """
    system_prompt = """You are a quality control coordinator. Your ONLY job is to call MCP server tools - you do NOT process data yourself.

**CRITICAL: YOU ARE A COORDINATOR, NOT A DATA PROCESSOR**
- The MCP server retrieves statistics and scrubs PII - you just coordinate
- NEVER attempt to read essay content or detect students yourself
- Your job: Call the MCP tools and present results to teacher

**CONTEXT FROM PREVIOUS PHASE:**
- Job ID: {job_id}
- Expected student count: {student_count}
- OCR processing: ✓ Complete (MCP server processed all files)

**YOUR TASKS (IN ORDER):**

**Task 1: Retrieve Student Manifest (MCP SERVER DOES THIS)**
- **CRITICAL**: Call get_job_statistics - do NOT attempt to read or parse essays yourself
- Call: get_job_statistics(job_id="{job_id}")
- The MCP server returns: Student list with names, page counts, word counts, status
- Parse the response to understand what students were detected

**Task 2: Present Manifest to Teacher**
- Show the teacher a clear list:
  ```
  I found X students:
  1. John Smith (3 pages, 450 words)
  2. Jane Doe (2 pages, 380 words)
  3. Unknown Student 01 (4 pages, 520 words)

  Does this look correct? Are all your students accounted for?
  ```
- **IMPORTANT**: Be clear about Unknown Students - explain they're essays without "Name: John Doe" at the top

**Task 3: Handle Teacher Response**
- If teacher says **YES** (approve):
  - Move to Task 4 (scrubbing)
- If teacher says **NO** (issues):
  - Explain name detection requirements:
    "For student names to be detected, essays must have 'Name: Full Name' at the TOP of the FIRST PAGE only.

    If students are missing:
    - Check if their names follow this format
    - Ensure the name appears at the top (not middle or bottom)
    - Make sure it's on the first page only

    Would you like me to explain how to retry processing with corrected files?"
  - Wait for teacher to decide next steps
  - If retry requested, explain they need to contact previous node (out of scope for this node)

**Task 4: Privacy Protection (MCP SERVER DOES THIS)**
Once teacher approves:
- **CRITICAL**: Call scrub_processed_job - do NOT attempt to scrub essays yourself
- Call: scrub_processed_job(job_id="{job_id}")
- The MCP server removes all student names from essay text for privacy during AI evaluation
- Confirm: "Great! The MCP server has removed student names for privacy. Ready to start grading..."
- Signal completion

**Task 5: Signal Completion**
- Call: complete_inspection(scrubbing_complete=True)
- This signals you're ready to move to evaluation phase

**CRITICAL ERROR HANDLING:**
If ANY tool fails:
1. STOP immediately
2. Report error clearly: "I encountered an error while [action]: [error message]"
3. Suggest fixes based on error type
4. DO NOT continue to next task
5. All operations MUST go through MCP server

**TOOLS AVAILABLE:**
- get_job_statistics(job_id)
- scrub_processed_job(job_id)
- complete_inspection (signals completion)

Always be helpful: "This checkpoint helps ensure all students were detected correctly before grading begins."
"""

    # Prepare context-aware prompt
    job_id = state.get("job_id") or "Unknown"
    student_count = state.get("student_count") or "Unknown"

    system_prompt = system_prompt.format(
        job_id=job_id,
        student_count=student_count,
    )

    # Get MCP tools
    from edagent.mcp_tools import get_grading_tools
    from langchain_core.tools import tool as tool_decorator

    tools = await get_grading_tools()

    # Add completion signal tool
    inspection_state = {"scrubbing_complete": False}

    @tool_decorator
    def complete_inspection(scrubbing_complete: bool) -> str:
        """Signal that inspection and scrubbing are complete.

        Args:
            scrubbing_complete: Whether PII scrubbing was successful

        Returns:
            Confirmation message
        """
        inspection_state["scrubbing_complete"] = scrubbing_complete
        return "✓ Student verification and privacy protection complete. Ready to evaluate essays."

    tools.append(complete_inspection)

    llm = get_llm().bind_tools(tools)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # Agentic loop
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

            print(
                f"[INSPECT_AND_SCRUB] Iteration {iteration}: Calling tool '{tool_name}' with args: {tool_args}",
                flush=True,
            )

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

    # Return state - route to next phase if complete, otherwise stay in this node
    if inspection_state.get("scrubbing_complete", False):
        return {
            "current_phase": "evaluate",
            "next_step": "evaluate_essays",
            "scrubbing_complete": inspection_state["scrubbing_complete"],
            "messages": messages[len(state["messages"]) :],
        }
    else:
        # Not done yet - route back to this node
        return {
            "next_step": "inspect_and_scrub",
            "messages": messages[len(state["messages"]) :],
        }


# --- NEW: Evaluate Essays Node (Phase 6 + 7) ---
async def evaluate_essays_node(state: AgentState) -> AgentState:
    """Evaluation node - retrieves context from knowledge base and grades essays.

    This node:
    1. (Conditional) Queries knowledge base if reading materials were added
    2. Calls evaluate_job with rubric, context, and system instructions
    3. Waits for evaluation to complete (may take several minutes)
    4. Moves to report generation phase

    Args:
        state: Current agent state with job_id and rubric

    Returns:
        Updated state with evaluation completion status
    """
    system_prompt = """You are an evaluation coordinator. Your ONLY job is to call MCP server tools - you do NOT grade essays yourself.

**CRITICAL: YOU ARE A COORDINATOR, NOT A GRADER**
- The MCP server does ALL the grading - you just coordinate the process
- NEVER write evaluations, feedback, or grades yourself
- NEVER summarize or interpret essay content
- Your job: Call evaluate_job and let the MCP server do the work

**CONTEXT FROM PREVIOUS PHASES:**
- Job ID: {job_id}
- Rubric: {rubric_status}
- Essay question: {question_status}
- Reading materials added to KB: {materials_added_to_kb}
- Student list verified: ✓
- Privacy protection: ✓

**YOUR TASKS (IN ORDER):**

**Task 1: Retrieve Context from Knowledge Base (CONDITIONAL)**
- If materials_added_to_kb is TRUE:
  - Derive a search query from the essay question and rubric
  - Example: Question "Analyze Frost's use of symbolism" + Rubric "Check for theme analysis"
    → Query: "Frost symbolism themes imagery poetic devices"
  - Create the same topic name used in prepare_essays_node:
    - If question_text exists: derive from question (e.g., "frost_poetry_essays")
    - Otherwise: use "general_essays"
  - Call: query_knowledge_base(query=<derived_query>, topic=<same_topic_from_prepare>)
  - Store the retrieved context for evaluation
  - Confirm: "I've retrieved relevant context from the reading materials..."
- If materials_added_to_kb is FALSE:
  - Skip this step
  - Use empty string for context_material
  - Confirm: "No reading materials were provided, so I'll grade based on the rubric alone..."

**Task 2: Grade Essays (MCP SERVER DOES THIS, NOT YOU)**
- **CRITICAL**: You MUST call evaluate_job - do NOT attempt to grade essays yourself
- **CRITICAL**: Do NOT read essay content, write feedback, or assign scores - that's the MCP server's job
- Call: evaluate_job(
    job_id="{job_id}",
    rubric=<rubric_text_from_state>,
    context_material=<from_KB_or_empty>,
    system_instructions=<question_text_from_state_or_None>
  )
- **CRITICAL**: Do NOT pass null/None for optional parameters - omit them entirely if not needed
- **NOTE**: The MCP server does the actual grading. This may take several minutes for large batches (e.g., 15 essays = 3-5 minutes)
- Explain to teacher: "Sending essays to the grading system with your rubric... This may take a few minutes for {student_count} students."
- Wait for completion - the MCP server is doing all the work

**Task 3: Confirm Completion**
- When evaluate_job returns successfully:
  - Confirm: "✓ Grading complete! The MCP server has evaluated all essays. Now generating reports..."
- Signal completion

**Task 4: Signal Completion**
- Call: complete_evaluation(evaluation_complete=True, context_material=<context_or_empty>)
- This signals you're ready to move to report generation

**CRITICAL ERROR HANDLING:**
If ANY tool fails:
1. STOP immediately
2. Report error clearly: "I encountered an error while [action]: [error message]"
3. Possible causes:
   - Knowledge base query fails: "Reading materials might not have been properly indexed"
   - Evaluation fails: "Could be an API timeout or model issue. Let's retry..."
4. DO NOT continue to next task
5. All operations MUST go through MCP server

**MCP TOOL PARAMETER RULES:**
- NEVER pass null/None for optional parameters
- If optional parameter is not needed, OMIT the key entirely
- Example CORRECT: evaluate_job(job_id="job_123", rubric="...", context_material="")
- Example WRONG: evaluate_job(job_id="job_123", rubric="...", context_material="", system_instructions=null)

**TOOLS AVAILABLE:**
- query_knowledge_base(query, topic) - Optional, only if materials_added_to_kb is True
- evaluate_job(job_id, rubric, context_material, system_instructions) - Required
- complete_evaluation - Signals completion

Always be patient: "Evaluation is running... This is the core grading step where I apply your rubric to each essay."
"""

    # Prepare context-aware prompt
    job_id = state.get("job_id") or "Unknown"
    rubric_status = "✓ Loaded" if state.get("rubric_text") else "❌ Missing"
    question_status = state.get("question_text") or "Not provided"
    materials_added_to_kb = state.get("materials_added_to_kb", False)
    student_count = state.get("student_count") or "Unknown"

    system_prompt = system_prompt.format(
        job_id=job_id,
        rubric_status=rubric_status,
        question_status=question_status,
        materials_added_to_kb=materials_added_to_kb,
        student_count=student_count,
    )

    # Get MCP tools
    from edagent.mcp_tools import get_grading_tools
    from langchain_core.tools import tool as tool_decorator

    tools = await get_grading_tools()

    # Add completion signal tool
    evaluation_state = {
        "evaluation_complete": False,
        "context_material": "",
    }

    @tool_decorator
    def complete_evaluation(evaluation_complete: bool, context_material: str) -> str:
        """Signal that evaluation is complete.

        Args:
            evaluation_complete: Whether evaluation was successful
            context_material: Retrieved context from knowledge base (or empty string)

        Returns:
            Confirmation message
        """
        evaluation_state["evaluation_complete"] = evaluation_complete
        evaluation_state["context_material"] = context_material
        return "✓ Evaluation complete. All essays have been graded. Ready to generate reports."

    tools.append(complete_evaluation)

    llm = get_llm().bind_tools(tools)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # Agentic loop
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

            print(
                f"[EVALUATE_ESSAYS] Iteration {iteration}: Calling tool '{tool_name}' with args: {tool_args}",
                flush=True,
            )

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

    # Return state - route to next phase if complete, otherwise stay in this node
    if evaluation_state.get("evaluation_complete", False):
        return {
            "current_phase": "report",
            "next_step": "generate_reports",
            "evaluation_complete": evaluation_state["evaluation_complete"],
            "context_material": evaluation_state["context_material"],
            "messages": messages[len(state["messages"]) :],
        }
    else:
        # Not done yet - route back to this node
        return {
            "next_step": "evaluate_essays",
            "messages": messages[len(state["messages"]) :],
        }


# --- NEW: Generate Reports Node (Phase 8) ---
async def generate_reports_node(state: AgentState) -> AgentState:
    """Report generation node - creates gradebook and student feedback files.

    This node:
    1. Calls generate_gradebook to create CSV
    2. Calls generate_student_feedback to create individual PDFs
    3. Calls download_reports_locally to get local file paths
    4. Shows download links to teacher
    5. Asks about emailing AND calls complete_grading_workflow (SAME TURN)
    6. Returns to router (which may route to email_distribution)

    Args:
        state: Current agent state with job_id and completed evaluation

    Returns:
        Updated state with job_id preserved for email routing
    """
    system_prompt = """You are a report coordinator. Your ONLY job is to call MCP server tools - you do NOT create reports yourself.

**CRITICAL: YOU ARE A COORDINATOR, NOT A REPORT WRITER**
- The MCP server generates ALL reports - you just coordinate the process
- NEVER write grades, feedback, or summaries yourself
- NEVER read or interpret evaluation results
- Your job: Call the report generation tools and present the download links

**CONTEXT FROM PREVIOUS PHASES:**
- Job ID: {job_id}
- Evaluation: ✓ Complete (MCP server graded all essays)
- All essays graded

**YOUR TASKS (IN ORDER):**

**Task 1: Generate Gradebook (MCP SERVER DOES THIS)**
- **CRITICAL**: Call generate_gradebook - do NOT create CSV yourself
- Call: generate_gradebook(job_id="{job_id}")
- The MCP server creates a CSV file with: Student Name, Grade, Feedback Summary
- Stored in database by MCP server
- Confirm: "Requesting gradebook from MCP server..."

**Task 2: Generate Student Feedback (MCP SERVER DOES THIS)**
- **CRITICAL**: Call generate_student_feedback - do NOT create PDFs yourself
- Call: generate_student_feedback(job_id="{job_id}")
- The MCP server creates individual PDF reports for each student
- All PDFs packaged into a ZIP file by MCP server
- Stored in database by MCP server
- Confirm: "Requesting individual feedback reports from MCP server..."

**Task 3: Download Reports to Local Files**
- Call: download_reports_locally(job_id="{job_id}")
- This downloads the MCP server's reports from database to local temp directory
- Returns: {{"gradebook_path": "/tmp/...", "feedback_zip_path": "/tmp/..."}}
- These are the paths the teacher can download from
- **IMPORTANT**: Use these LOCAL paths in your response to teacher

**Task 4: Present Results to Teacher**
- Show the download links in this EXACT format:
  ```
  Your grading is complete! Here are your results:

  📊 Gradebook: [gradebook_path from download_reports_locally]

  📄 Student Feedback: [feedback_zip_path from download_reports_locally]

  Both files are ready for download using the download buttons above.
  ```

**Task 5: Ask About Email AND Call Tool (SAME TURN - CRITICAL!)**
- **After presenting results**, in the SAME response:
  1. Ask: "Would you like me to email these feedback reports to your students?"
  2. **IMMEDIATELY call**: complete_grading_workflow(job_id="{job_id}", route_to_email=False)
     - Use route_to_email=False (teacher hasn't confirmed yet)
     - This saves job_id so router can access it when teacher responds

**WHY THIS IS CRITICAL:**
- You MUST call complete_grading_workflow BEFORE teacher responds
- The grading workflow ends after you ask the question
- When teacher responds "email students", router needs job_id to route correctly
- If you don't call the tool now, job_id will be None and email will fail

**EXAMPLE - Your response must include BOTH:**
```
"Your grading is complete! Here are your results:
[download links]
Would you like me to email these feedback reports to your students?"

AND tool_calls: [complete_grading_workflow(job_id="{job_id}", route_to_email=False)]
```

**CRITICAL ERROR HANDLING:**
If ANY tool fails:
1. STOP immediately
2. Report error clearly: "I encountered an error while [action]: [error message]"
3. Possible causes:
   - generate_gradebook fails: "Grading data might not be saved correctly"
   - generate_student_feedback fails: "Could be a PDF generation issue"
   - download_reports_locally fails: "Reports might not be in database"
4. DO NOT skip to asking about email if reports failed
5. All operations MUST go through MCP server

**TOOLS AVAILABLE:**
- generate_gradebook(job_id)
- generate_student_feedback(job_id)
- download_reports_locally(job_id)
- complete_grading_workflow(job_id, route_to_email) - MUST call this!

Always be celebratory: "Congratulations! All essays have been graded and reports are ready!"
"""

    # Prepare context-aware prompt
    job_id = state.get("job_id") or "Unknown"

    system_prompt = system_prompt.format(job_id=job_id)

    # Get MCP tools
    from edagent.mcp_tools import get_grading_tools
    from langchain_core.tools import tool as tool_decorator

    tools = await get_grading_tools()

    # Add routing control tool
    routing_state = {"next_step": None, "job_id": None, "workflow_complete": False}

    @tool_decorator
    def complete_grading_workflow(job_id: str, route_to_email: bool) -> str:
        """Complete the grading workflow and set routing for next step.

        Args:
            job_id: The job ID from the grading process
            route_to_email: Whether to route to email distribution (True) or end (False)

        Returns:
            Confirmation message
        """
        routing_state["job_id"] = job_id
        routing_state["workflow_complete"] = True
        routing_state["next_step"] = "email_distribution" if route_to_email else "END"
        if route_to_email:
            return f"✓ Routing configured: Proceeding to email distribution with job_id={job_id}"
        else:
            return f"✓ Workflow complete for job_id={job_id}. Job ID saved for potential email routing."

    tools.append(complete_grading_workflow)

    llm = get_llm().bind_tools(tools)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # Agentic loop
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

            print(
                f"[GENERATE_REPORTS] Iteration {iteration}: Calling tool '{tool_name}' with args: {tool_args}",
                flush=True,
            )

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

    # Return state - route to next step if workflow complete, otherwise stay in this node
    if routing_state.get("workflow_complete", False):
        return {
            "next_step": routing_state["next_step"],
            "job_id": routing_state["job_id"],
            "messages": messages[len(state["messages"]) :],
        }
    else:
        # Not done yet - route back to this node
        return {
            "next_step": "generate_reports",
            "messages": messages[len(state["messages"]) :],
        }


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

**Grading Pipeline for Tests:**
1. Gather materials (answer key, test PDFs)
2. Process documents with batch_process_documents
3. Review student manifest with get_job_statistics
4. Scrub student names with scrub_processed_job
5. Evaluate with evaluate_job (using answer key)
6. Generate reports with generate_gradebook and generate_student_feedback
7. **Download reports locally**: Call download_reports_locally(job_id=<job_id>)
   - This downloads reports from database to local temp directory
   - Provide the LOCAL file paths from this tool to the teacher
8. **Ask Teacher AND Call Tool (SAME TURN!)**:
   - **CRITICAL - Do BOTH in the SAME response:**
     1. Ask: "Would you like me to email these feedback reports to your students?"
     2. **IMMEDIATELY call**: complete_grading_workflow(job_id="<job_id_from_step_7>", route_to_email=False)
        - Use the EXACT job_id from download_reports_locally response
        - Use route_to_email=False (teacher hasn't confirmed yet)
        - This saves job_id so router can access it when teacher responds with "email students"

**CRITICAL: You MUST call complete_grading_workflow BEFORE the teacher responds, otherwise job_id will be None and email will fail!**

Your grading is more objective and score-focused than essay grading. You check for correctness, not writing quality.

Always be fair and consistent in applying answer keys."""

    # Get grading-specific tools
    tools = await get_grading_tools()

    # Add file reading tool and routing control
    from langchain_core.tools import tool as tool_decorator

    routing_state = {"next_step": "END", "job_id": None}

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

    @tool_decorator
    def complete_grading_workflow(job_id: str, route_to_email: bool) -> str:
        """Complete the grading workflow and set routing for next step.

        Args:
            job_id: The job ID from the grading process
            route_to_email: Whether to route to email distribution (True) or end (False)

        Returns:
            Confirmation message
        """
        routing_state["job_id"] = job_id
        routing_state["next_step"] = "email_distribution" if route_to_email else "END"
        if route_to_email:
            return f"✓ Routing configured: Proceeding to email distribution with job_id={job_id}"
        else:
            return f"✓ Workflow complete for job_id={job_id}"

    tools = tools + [read_file, complete_grading_workflow]

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

            # Debug logging
            print(f"[TEST_GRADING] Iteration {iteration}: Calling tool '{tool_name}' with args: {tool_args}", flush=True)

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

    # Return the final state with routing decision
    return {
        "next_step": routing_state["next_step"],
        "job_id": routing_state["job_id"],
        "messages": messages[len(state["messages"]) :],
    }


# --- Email Distribution Node ---
async def email_distribution_node(state: AgentState) -> AgentState:
    """Email distribution node for sending graded feedback to students.

    This node handles the complete email distribution workflow:
    1. Identify students with email/name matching problems
    2. Teacher-in-the-loop correction for mismatched names
    3. Send emails automatically using roster and credentials

    Args:
        state: Current agent state (must contain job_id)

    Returns:
        Updated state with email distribution results
    """
    # Extract job_id from state
    job_id_from_state = state.get("job_id")

    # DEBUG: Print what email node receives
    print(f"[EMAIL NODE DEBUG] Received state keys: {state.keys()}")
    print(f"[EMAIL NODE DEBUG] job_id from state: {job_id_from_state}")
    print(f"[EMAIL NODE DEBUG] Full state: {dict(state)}")

    # Safety check: ensure we have a job_id
    if not job_id_from_state:
        from langchain_core.messages import AIMessage
        return {
            "next_step": "END",
            "messages": [
                AIMessage(
                    content="⚠️ Error: No job_id was provided from the grading workflow. "
                    "Please complete a grading task first before attempting to send emails."
                )
            ],
        }

    system_prompt = f"""You are an automated email distribution system. A grading job (job_id: {job_id_from_state}) has completed.

**YOUR ONLY TASK: CALL ONE TOOL AND REPORT THE RESULTS**

The email system is fully automatic and handles:
- ✓ Student name matching (with fuzzy matching for OCR errors)
- ✓ Email address lookup from roster
- ✓ PDF retrieval from database
- ✓ Email sending with attachments
- ✓ Logging and error handling

**WORKFLOW:**

**Step 1: Send emails (ONE TIME ONLY)**
- Call send_student_feedback_emails(job_id="{job_id_from_state}") EXACTLY ONCE
- **DO NOT call this tool multiple times - ONE call only!**
- The tool returns a summary of sent/skipped students

**Step 2: Report results and STOP**
- After the tool returns (even if errors occurred), report the results and STOP
- Report format:
  - If emails sent: "✓ Sent feedback emails to X students"
  - If students skipped: "⚠ Skipped Y students: [names and reasons]"
  - If errors: "⚠ Error: [error message]"
- Then STOP - do not call any more tools

**CRITICAL RULES:**
1. Call send_student_feedback_emails ONLY ONCE - never retry or call again
2. NEVER use any other tools (identify_email_problems, verify_student_name_correction, etc.)
3. After getting the tool response, report results and STOP immediately
4. Do not ask for confirmation, email addresses, or any other information
5. The tool handles all name matching automatically - no teacher intervention needed"""

    # Get email-specific tools from MCP server
    from edagent.mcp_tools import get_email_tools

    tools = await get_email_tools()

    llm = get_llm().bind_tools(tools)

    # Add a forcing message to trigger immediate action
    from langchain_core.messages import AIMessage

    messages = [
        SystemMessage(content=system_prompt),
    ] + list(state["messages"]) + [
        AIMessage(content=f"I'll send the feedback emails now for job {job_id_from_state}.")
    ]

    # Agentic loop for email workflow
    max_iterations = 20  # May need multiple rounds for teacher-in-the-loop
    iteration = 0

    while iteration < max_iterations:
        response = await llm.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            # No more tool calls, workflow complete
            break

        from langchain_core.messages import ToolMessage

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            # Debug logging
            print(f"[EMAIL_DISTRIBUTION] Iteration {iteration}: Calling tool '{tool_name}' with args: {tool_args}", flush=True)

            # Inject job_id from state if not provided or is None
            if "job_id" in tool_args:
                if tool_args["job_id"] is None or tool_args["job_id"] == "":
                    tool_args["job_id"] = state.get("job_id")
            # If tool expects job_id but didn't include it, add it
            # Check if this tool has a job_id parameter by trying to invoke with it
            else:
                # Add job_id for email tools (they all expect it)
                tool_args["job_id"] = state.get("job_id")

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
) -> Literal[
    "gather_materials",
    "prepare_essays",
    "inspect_and_scrub",
    "evaluate_essays",
    "generate_reports",
    "test_grading",
    "general",
    "email_distribution",
    "router",
    "END",
]:
    """Conditional edge function that determines which expert to route to.

    Args:
        state: Current agent state

    Returns:
        Name of the next node to execute
    """
    return state["next_step"]