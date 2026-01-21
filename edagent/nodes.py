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

    # Check for email distribution intent FIRST (before phase routing)
    # This allows users to confirm email after grading completes

    # FIRST: Check for negative responses (user declining email)
    negative_keywords = ["no", "don't", "dont", "skip", "not", "nope", "nah", "decline", "cancel"]

    # THEN: Check for positive email intent
    email_keywords = ["email", "send", "distribute", "mail"]
    positive_keywords = ["yes", "yeah", "yep", "sure", "ok", "okay"]

    # If user says "no email" or similar, DON'T route to email
    if job_id and current_phase == "report":
        has_negative = any(neg in last_message for neg in negative_keywords)
        has_email_keyword = any(keyword in last_message for keyword in email_keywords)
        has_positive = any(keyword in last_message for keyword in positive_keywords)

        # Route to email ONLY if:
        # - User has positive confirmation (yes/send/etc.) OR
        # - User mentions email WITHOUT a negative
        should_email = (has_positive or has_email_keyword) and not has_negative

        if should_email:
            # User has a completed grading job and is confirming email distribution
            return {
                "next_step": "email_distribution",
                "job_id": job_id,  # CRITICAL: Pass through the job_id
                "messages": [AIMessage(content=f"Great! Let me help you distribute these via email. (Using job_id: {job_id})")],
            }

    # Check if we're continuing a workflow (phase in progress)
    phase_routing = {
        "gather": "gather_materials",
        "prepare": "prepare_essays",
        "validate": "validate_student_names",  # New: name validation phase
        "scrub": "scrub_pii",                  # New: PII scrubbing phase
        "inspect": "inspect_and_scrub",        # Legacy: old combined phase
        "evaluate": "evaluate_essays",
        "report": "generate_reports",
    }

    if current_phase and current_phase in phase_routing:
        # Continue the workflow at the current phase
        next_node = phase_routing[current_phase]
        return {
            "next_step": next_node,
            "messages": [],  # Don't add routing message, let the phase node handle it
        }

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
5. **NEVER ask for:** job name, student count, or essay format - these are auto-generated/detected

**PHASE 0: PRESENT OVERVIEW (ONLY IF current_phase IS None)**

If this is the FIRST message (current_phase is None), present this overview:

"Great! I'd love to help you grade those essays. Here's how this works—there are a few things I might need to give you the best grading results:

**Required:**
- **A grading rubric** - This tells me what criteria to use when evaluating the essays
- **The student essays** - I'll use OCR to extract the text, so handwritten or typed both work

**Optional (but helpful for better grading):**
- **The essay question or prompt** - If students were answering a specific question, this gives me important context
- **Reading materials or lecture notes** - If students were supposed to reference specific sources, I can use them to check accuracy and provide context-aware grading

**Supported file formats:** PDF, text files (.txt, .md), images (JPG/PNG), ZIP files
**Note:** Google Docs shortcuts (.gdoc) and Word documents (.docx) are not supported—please convert/export to PDF or .txt first.

You don't have to give me all this at once! Let me help you upload what you need, one piece at a time.

Let's start: Do you have a grading rubric? You can upload it with 📎 or paste it directly here."

**PHASE 1: GATHER MATERIALS ONE AT A TIME**

**Step 1: Rubric (REQUIRED)**
- If overview was just presented, wait for rubric upload/paste
- If they upload a file:
  - **PDF file (.pdf)**: Call convert_pdf_to_text(file_path=<path>) → Extract response["text_content"]
  - **Text file (.txt, .md)**: Call read_text_file(file_path=<path>) → Extract response["text_content"]
  - The text_content is the rubric text to use
- If they paste text: Use it directly as the rubric text
- When received, acknowledge briefly and move to Step 2

**Step 2: Essay Question/Prompt (OPTIONAL)**
- Ask: "Perfect! Was there a specific essay question or prompt the students had to answer? If so, you can share it here or upload it with 📎"
- If YES and they upload a file:
  - **PDF file (.pdf)**: Call convert_pdf_to_text(file_path=<path>) → Extract response["text_content"]
  - **Text file (.txt, .md)**: Call read_text_file(file_path=<path>) → Extract response["text_content"]
  - The text_content is the question text to use
- If YES and they paste: Use it directly as the question text
- If NO: "No problem! Moving on..." (question_text will be None/omitted)
- Move to Step 3

**Step 3: Reading Materials / Context (OPTIONAL)**
- Ask: "Did students use any specific reading materials, textbook chapters, articles, or lecture notes for these essays?"
- If YES: "Great! Can you upload those materials now? I'll add them to my knowledge base to provide context-aware grading. Just use the 📎 button."
- **WAIT for upload - DO NOT proceed until you have the files**
- **NEVER search online** - only use what teacher uploads
- When files are received:
  - **STEP 3A:** Create a simple topic name (e.g., "WR121_Essays_Context" or "Frost_Poetry_Materials")
  - **STEP 3B:** **IMMEDIATELY** call: add_to_knowledge_base(file_paths=[<all_uploaded_paths>], topic=<topic_name>)
  - **STEP 3C:** Wait for success response from add_to_knowledge_base
  - **STEP 3D:** Store the topic name - you MUST pass it to create_job_with_materials later
  - **CRITICAL:** You MUST call add_to_knowledge_base when context materials are provided - do NOT skip this!
- If NO: "No problem! I'll grade based on the rubric alone."
- Move to completion

**Step 4: Create Job (DO NOT ASK ANY QUESTIONS)**
- **CRITICAL:** DO NOT ask the user for job name, student count, or essay format
- Simply call create_job_with_materials with the materials you've gathered
- The MCP server will auto-generate a job_id and handle all metadata

**TOOLS AVAILABLE:**
- convert_pdf_to_text: Read PDF files - returns text_content
- read_text_file: Read plain text files (.txt, .md) - returns text_content
- convert_image_to_pdf: Convert images to PDF format
- add_to_knowledge_base: Add context materials to RAG (reading materials, textbooks, etc.)

**FILE HANDLING:**
When files are attached, you'll see: "[User attached files: /path1, /path2...]"
- For rubric/question files:
  - **If .pdf**: Use convert_pdf_to_text(file_path=<path>)
  - **If .txt or .md**: Use read_text_file(file_path=<path>)
  - Both return text_content in the response
- For reading materials: Use add_to_knowledge_base(file_paths=[...], topic=<topic_name>)
- **CRITICAL**: Choose the correct tool based on file extension - ALL file processing MUST use MCP tools

**STATE UPDATES:**
As you collect materials, update the state:
- rubric_text: Store rubric content
- question_text: Store question content (or None)
- knowledge_base_topic: Store the topic name used for add_to_knowledge_base (or None if no materials)

**EXIT CONDITION:**
Once you have:
- ✓ Rubric (extracted text)
- ✓ Question (or confirmed not needed)
- ✓ Reading materials processed:
  - If materials were provided: add_to_knowledge_base was called AND topic name is stored
  - If not provided: No action needed

**CRITICAL:** Call the `create_job_with_materials` MCP tool with ALL gathered materials:

**PRE-FLIGHT CHECK before calling create_job_with_materials:**
1. Do I have rubric text? (Required)
2. If user uploaded context materials, did I call add_to_knowledge_base? (If not, STOP and call it now!)
3. Do I have the topic name if materials were added? (Required if Step 2 was yes)

**IMPORTANT - How to call create_job_with_materials:**
- **NEVER ask the user for:** job name, student count, or essay format - these are all optional/auto-detected
- **DO NOT include:** job_name, student_count, or essay_format parameters - let MCP auto-generate/detect them
- **Only provide:** rubric (required), question_text (if user provided it), knowledge_base_topic (if materials were added)

**Example when question AND context are provided:**
```
create_job_with_materials(
    rubric="<the rubric text>",
    question_text="Analyze themes in Frost's poetry",
    knowledge_base_topic="WR121_Essays"  # Topic used when adding to knowledge base
)
```

**Example when only rubric is provided:**
```
create_job_with_materials(
    rubric="<the rubric text>"
)
```

**CRITICAL:** If you called add_to_knowledge_base earlier, you MUST pass the same topic name to knowledge_base_topic parameter!

This creates a job in the edmcp database with all materials and returns a job_id.

**IMPORTANT NOTES:**
- The MCP server (edmcp) handles ALL data storage:
  - Rubric/question → SQLite database via create_job_with_materials
  - Reading materials → Knowledge base (RAG) via add_to_knowledge_base **YOU MUST CALL THIS!**
- You are ONLY a coordinator - the MCP server does the actual storage
- After calling create_job_with_materials, extract the job_id from the response
- Be encouraging and patient
- Celebrate progress: "Great! Got the rubric. Now let's talk about..."
- Keep responses SHORT and CONTEXTUAL
- NEVER repeat the overview after the first message

**CRITICAL SEQUENCE WHEN CONTEXT MATERIALS ARE PROVIDED:**
1. User uploads context files → You see file paths
2. **IMMEDIATELY** call add_to_knowledge_base(file_paths=[...], topic="...")
3. Wait for success response
4. Store the topic name
5. THEN call create_job_with_materials with knowledge_base_topic parameter

**FAILURE MODE TO AVOID:**
❌ BAD: User uploads context → You skip add_to_knowledge_base → Context is LOST
✅ GOOD: User uploads context → You call add_to_knowledge_base → Store topic → Pass to create_job_with_materials"""

    # Get MCP tools for file conversion and processing
    from edagent.mcp_tools import get_phase_tools

    # Get MCP tools for gather phase only
    tools = await get_phase_tools("gather")

    llm = get_llm().bind_tools(tools)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # Agentic loop: Allow multiple rounds of gathering
    max_iterations = 10
    iteration = 0

    # Track what we've gathered and the job_id from create_job_with_materials
    gathered_state = {
        "job_id": state.get("job_id"),  # Track job_id from create_job_with_materials
        "rubric_text": state.get("rubric_text"),
        "question_text": state.get("question_text"),
        "knowledge_base_topic": state.get("knowledge_base_topic"),  # Topic name if materials were added to KB
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

            print(f"[GATHER_MATERIALS] Iteration {iteration}: Calling tool '{tool_name}'", flush=True)

            # Find and execute the tool
            matching_tool = next((t for t in tools if t.name == tool_name), None)
            if matching_tool:
                try:
                    result = await matching_tool.ainvoke(tool_args)

                    # Special handling for add_to_knowledge_base - track topic
                    if tool_name == "add_to_knowledge_base":
                        import json
                        try:
                            result_dict = json.loads(result) if isinstance(result, str) else result
                            print(f"[GATHER_MATERIALS] add_to_knowledge_base result: {result_dict}", flush=True)

                            if result_dict.get("status") == "success":
                                topic = result_dict.get("topic")
                                gathered_state["knowledge_base_topic"] = topic
                                gathered_state["materials_added_to_kb"] = True  # Set flag for evaluate phase
                                print(f"[GATHER_MATERIALS] Context materials added to knowledge base. Topic: {topic}", flush=True)
                            else:
                                print(f"[GATHER_MATERIALS] ERROR: add_to_knowledge_base failed: {result_dict}", flush=True)
                        except Exception as e:
                            print(f"[GATHER_MATERIALS] ERROR: Failed to parse add_to_knowledge_base result: {e}", flush=True)
                            print(f"[GATHER_MATERIALS] Raw result: {result}", flush=True)

                    # Special handling for create_job_with_materials - extract job_id and track question
                    if tool_name == "create_job_with_materials":
                        import json
                        try:
                            # Track question_text from the tool call arguments
                            if "question_text" in tool_args and tool_args["question_text"]:
                                gathered_state["question_text"] = tool_args["question_text"]
                                print(f"[GATHER_MATERIALS] Tracked question_text from create_job_with_materials", flush=True)

                            # Track rubric_text from the tool call arguments
                            if "rubric" in tool_args and tool_args["rubric"]:
                                gathered_state["rubric_text"] = tool_args["rubric"]
                                print(f"[GATHER_MATERIALS] Tracked rubric_text from create_job_with_materials", flush=True)

                            result_dict = json.loads(result) if isinstance(result, str) else result
                            print(f"[GATHER_MATERIALS] create_job_with_materials result: {result_dict}", flush=True)

                            if result_dict.get("status") == "success":
                                job_id = result_dict.get("job_id")
                                gathered_state["job_id"] = job_id
                                gathered_state["materials_complete"] = True  # Signal completion
                                print(f"[GATHER_MATERIALS] Successfully extracted job_id: {job_id}", flush=True)
                            else:
                                print(f"[GATHER_MATERIALS] ERROR: create_job_with_materials failed: {result_dict}", flush=True)
                        except Exception as e:
                            # If parsing fails, log the error
                            print(f"[GATHER_MATERIALS] ERROR: Failed to parse create_job_with_materials result: {e}", flush=True)
                            print(f"[GATHER_MATERIALS] Raw result: {result}", flush=True)

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


# --- NEW: Prepare Essays Node (Phase 2) ---
async def prepare_essays_node(state: AgentState) -> AgentState:
    """Prepare essays node - handles essay file uploads and OCR processing.

    This node:
    1. Asks user to upload essays
    2. Prepares files (handles ZIP, images, PDFs)
    3. Runs OCR via batch_process_documents
    4. Moves to inspection phase

    Args:
        state: Current agent state with job_id from gather phase

    Returns:
        Updated state with OCR completion status
    """
    try:
        # Check if OCR was already completed - prevent duplicate processing
        if state.get("ocr_complete", False):
            print("[PREPARE_ESSAYS] OCR already complete, skipping to validation phase", flush=True)
            return {
                "next_step": "validate_student_names",
                "current_phase": "validate",
                "messages": [],
            }

        system_prompt = """You are an essay preparation coordinator. Your job is to get the student essays uploaded and processed with OCR.

**CRITICAL: YOU ARE A COORDINATOR, NOT A PROCESSOR**
- The MCP server does ALL file processing and OCR - you just coordinate
- NEVER attempt to read, extract, or process essay content yourself
- Your job: Call the tools to prepare and process files

**CONTEXT FROM PREVIOUS PHASE:**
The teacher has already provided:
- ✓ Rubric (stored in database under job_id: {job_id})
- ✓ Essay question (if provided)
- ✓ Reading materials (if provided - already in knowledge base)

Now you need to get the STUDENT ESSAYS uploaded and processed.

**YOUR TASKS (IN ORDER):**

**Task 1: Check for Uploaded Essays**
- **FIRST**: Check the latest message for file attachments
- If you see "[User attached files: /path1, /path2...]" → SKIP to Task 2 immediately
- If NO files attached yet:
  - Ask: "Great! Now I'm ready for the student essays. Please upload them using 📎"
  - Mention: "I accept PDFs, Images (JPG/PNG), or ZIP files containing essays"
  - **IMPORTANT**: Warn that Google Docs (.gdoc) and Word documents (.docx) are NOT supported - they must export as PDF first
  - **DO NOT ask how many students or essays** - the OCR will auto-detect this
  - STOP and wait for user to upload

**Task 2: Handle File Uploads**
You will see: "[User attached files: /path1, /path2...]" in the message

- **Step 2A**: Call prepare_files_for_grading(file_paths=[...])
  - This local helper handles PDFs, ZIPs (auto-extracts), images (converts to PDF)
  - Returns JSON: {{"directory_path": "/tmp/...", "file_count": X, "warnings": [...]}}

- **Step 2B: CHECK WARNINGS**
  - Parse the JSON response
  - If warnings list is NOT empty:
    - Report to user: "I couldn't process these files: [list warnings]"
    - Explain how to fix (export Google Docs as PDF, etc.)
    - Ask if they want to upload corrected files or proceed
  - Only continue to Task 3 if warnings are empty OR user confirms to proceed

- **Step 2C**: Store the directory_path from the response for next task

**Task 3: Run OCR Processing**
- **CRITICAL**: Use the job_id from state (created in gather phase): {job_id}
- Call: batch_process_documents(directory_path=<from_prepare_files>, job_id="{job_id}")
  - Use the directory_path from prepare_files_for_grading response
  - Use the job_id from state (the job already exists in database!)
  - **CRITICAL**: Do NOT pass dpi parameter - omit it entirely
  - Returns: {{"job_id": "job_...", "total_documents": X, "students_detected": Y, "summary": {{...}}}}

- **Explain results** based on the summary:
  - "Processed X essays and found Y student records"
  - Mention if OCR or fast text extraction was used

**Task 4: Signal Completion**
- Call: complete_preparation(job_id="{job_id}", clean_directory_path=<path>)
- This signals you're ready to move to inspection phase

**CRITICAL - DO NOT VALIDATE NAMES IN THIS PHASE:**
- DO NOT ask the teacher to upload a roster or class list
- DO NOT ask for student name corrections
- DO NOT ask the teacher to verify the detected names
- Name validation happens AUTOMATICALLY in the next phase using the school roster
- Your ONLY job is to process the essays and signal completion
- Even if you see "Unknown Student" or unusual names - DO NOT mention it, validation happens next

**CRITICAL ERROR HANDLING:**
If ANY tool fails:
1. STOP immediately
2. Report error: "I encountered an error while [action]: [error message]"
3. Suggest fixes:
   - File issues: "Try re-uploading or checking file format"
   - OCR issues: "Files might be corrupted or password-protected"
4. DO NOT continue to next task

**TOOLS AVAILABLE:**
- prepare_files_for_grading(file_paths) - Local helper to stage files
- batch_process_documents(directory_path, job_id) - MCP tool to extract text
- complete_preparation(job_id, clean_directory_path) - Signal completion

Always be brief: "✓ Essays processed successfully! Moving to validation..."
"""

        # Prepare context-aware prompt with job_id
        job_id = state.get("job_id") or "NOT_SET"

        print(f"[PREPARE_ESSAYS] Starting prepare phase. Job ID: {job_id}", flush=True)

        system_prompt = system_prompt.format(job_id=job_id)

        # Get MCP tools
        from edagent.mcp_tools import get_phase_tools
        from edagent.file_utils import prepare_files_for_grading
        from langchain_core.tools import tool as tool_decorator

        print(f"[PREPARE_ESSAYS] Getting phase tools for 'prepare'", flush=True)
        tools = await get_phase_tools("prepare")
        print(f"[PREPARE_ESSAYS] Got {len(tools)} MCP tools", flush=True)

        # Add file preparation utility
        tools.append(prepare_files_for_grading)

        # Add completion signal tool
        preparation_state = {
            "clean_directory_path": None,
            "ocr_complete": False,
        }

        @tool_decorator
        def complete_preparation(job_id: str, clean_directory_path: str) -> str:
            """Signal that essay preparation and OCR processing is complete.

            Args:
                job_id: The job ID from batch_process_documents (should match state)
                clean_directory_path: Path to prepared files directory

            Returns:
                Confirmation message
            """
            preparation_state["clean_directory_path"] = clean_directory_path
            preparation_state["ocr_complete"] = True
            return f"✓ Essay preparation complete. Job ID: {job_id}. Ready to inspect student list."

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

                print(f"[PREPARE_ESSAYS] Iteration {iteration}: Calling tool '{tool_name}'", flush=True)

                matching_tool = next((t for t in tools if t.name == tool_name), None)
                if matching_tool:
                    try:
                        result = await matching_tool.ainvoke(tool_args)

                        # Special handling for batch_process_documents - extract student count
                        if tool_name == "batch_process_documents":
                            import json
                            try:
                                result_dict = json.loads(result) if isinstance(result, str) else result
                                print(f"[PREPARE_ESSAYS] batch_process_documents result: {result_dict}", flush=True)

                                if result_dict.get("status") == "success" and "students_detected" in result_dict:
                                    student_count = result_dict["students_detected"]
                                    preparation_state["student_count"] = student_count
                                    print(f"[PREPARE_ESSAYS] Captured student_count: {student_count}", flush=True)
                            except Exception as e:
                                print(f"[PREPARE_ESSAYS] ERROR: Failed to parse batch_process_documents result: {e}", flush=True)

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
            return_state = {
                "current_phase": "validate",
                "next_step": "validate_student_names",
                "clean_directory_path": preparation_state["clean_directory_path"],
                "ocr_complete": preparation_state["ocr_complete"],
                "messages": messages[len(state["messages"]) :],
            }
            # Add student_count if it was captured
            if "student_count" in preparation_state:
                return_state["student_count"] = preparation_state["student_count"]
            return return_state
        else:
            # Not done yet - wait for user to upload essays
            return {
                "next_step": "END",
                "current_phase": "prepare",  # Stay in prepare phase
                "messages": messages[len(state["messages"]) :],
            }
    except Exception as e:
        print(f"[PREPARE_ESSAYS] ERROR: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        from langchain_core.messages import AIMessage
        return {
            "next_step": "END",
            "messages": [AIMessage(content=f"I encountered an error in the prepare phase: {str(e)}\n\nPlease check the logs for more details.")],
        }


# --- NEW: Inspect and Scrub Node (Phase 3) ---
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
    try:
        system_prompt = """You are a quality control coordinator. Your ONLY job is to call MCP server tools - you do NOT process data yourself.

**CRITICAL: YOU ARE A COORDINATOR, NOT A DATA PROCESSOR**
- The MCP server retrieves statistics, validates names, and scrubs PII - you just coordinate
- NEVER attempt to read essay content or detect students yourself
- Your job: Call the MCP tools and present results to teacher

**CONTEXT FROM PREVIOUS PHASE:**
- Job ID: {job_id}
- Expected student count: {student_count}
- OCR processing: ✓ Complete (MCP server processed all files)

**NOTE ABOUT DUPLICATES:**
- If OCR was run multiple times on the same files, there may be duplicate essay records in the database
- Each duplicate has a different essay_id but identical content
- You must correct ALL instances - validate_student_names will find each one that needs correction
- This is normal behavior - just continue the correction process until all are fixed

**YOUR TASKS (IN ORDER):**

**WORKFLOW OVERVIEW:**
1. Call get_job_statistics (show what was detected)
2. Call validate_student_names (check against roster)
3. If mismatches found → correct them → IMMEDIATELY re-validate in same turn
4. If all validated → scrub → complete

**Task 1: Retrieve Student Manifest (MCP SERVER DOES THIS)**
- **CRITICAL**: Call get_job_statistics - do NOT attempt to read or parse essays yourself
- Call: get_job_statistics(job_id="{job_id}")
- The MCP server returns: Student list with names, page counts, word counts, status
- Parse the response to understand what students were detected

**Task 2: Validate Student Names Against Roster (MCP SERVER DOES THIS)**
- **CRITICAL**: Call validate_student_names - do NOT try to validate names yourself
- Call: validate_student_names(job_id="{job_id}")
- The MCP server checks each detected name against the school roster CSV
- Returns:
  - matched_students: Names that are in the roster (✓ Good)
  - mismatched_students: Names NOT in roster (⚠ Need correction)
  - total_missing: Count of roster students with no essay (just a number, NOT a list)

**Task 3: Present Results to Teacher**
- If ALL names validated (status="validated"):
  - Show summary: "✓ All X students validated successfully!"
  - List the matched students (name and word count)
  - **IMPORTANT**: Do NOT list missing students by name - only mention the count if > 0
  - If total_missing > 0, note: "(Note: X roster students did not submit essays)"
  - Ask: "Ready to proceed with scrubbing?"

- If MISMATCHES found (status="needs_corrections"):
  - Show ALL mismatched essays clearly:
    ```
    ⚠ Found X name(s) that need correction:

    1. Essay ID 49: "Unknown Student 01" - Not found in roster
       Essay preview: "[Show the essay_preview from the mismatched_students data]"

    2. Essay ID 52: "Unknown Student 02" - Not found in roster
       Essay preview: "[Show the essay_preview from the mismatched_students data]"

    Please provide the correct names for these students, one at a time or all at once.
    ```
  - **CRITICAL**:
    - Show essay_preview for each so teacher can identify physical essays
    - List ALL mismatched students at once
    - If there are duplicate essay IDs with similar content, mention this may be due to re-processing
  - Wait for teacher to provide the corrected name(s)
  - Do NOT proceed to scrubbing until all names are corrected

**Task 4: Correct Mismatched Names (IF NEEDED)**
- The teacher will provide corrected name(s) - either one at a time or multiple
- For EACH correction provided:
  1. Call: correct_detected_name(job_id="{job_id}", essay_id=<id>, corrected_name="<teacher_provided_name>")
  2. The tool verifies the name exists in roster and updates the database
  3. If successful, acknowledge: "✓ Essay ID X updated to [Name]"
  4. If the name is not in roster, the tool will suggest similar names - ask teacher to clarify
- **CRITICAL - IMMEDIATELY after ALL corrections in this turn:**
  - **DO NOT just say you'll re-check - ACTUALLY call the tool NOW**
  - Call: validate_student_names(job_id="{job_id}") in the SAME turn
  - Check the status in the response
- If validate_student_names returns status="validated":
  - ALL names are now correct! Proceed to Task 5 (scrubbing)
- If validate_student_names still shows status="needs_corrections":
  - Show the remaining mismatches (these might be different essay IDs that also need correction)
  - End the turn and wait for teacher to provide more corrections
  - Continue the correction process in next turn
- Only proceed to Task 5 when validate_student_names returns status="validated"

**Task 5: Privacy Protection (MCP SERVER DOES THIS)**
Once ALL names are validated:
- **CRITICAL**: Call scrub_processed_job - do NOT attempt to scrub essays yourself
- Call: scrub_processed_job(job_id="{job_id}")
- The MCP server removes all student names from essay text for privacy during AI evaluation
- Confirm: "✓ Privacy protection complete. Student names have been scrubbed. Ready to start grading..."
- Signal completion

**Task 6: Signal Completion**
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
- validate_student_names(job_id)
- correct_detected_name(job_id, essay_id, corrected_name)
- scrub_processed_job(job_id)
- complete_inspection (signals completion)

**IMPORTANT WORKFLOW RULE:**
DO NOT call scrub_processed_job until validate_student_names returns status="validated"!
All name mismatches must be corrected first.

Always be helpful: "This checkpoint helps ensure all students are correctly identified before grading begins."
"""

        # Prepare context-aware prompt
        job_id = state.get("job_id") or "Unknown"
        student_count = state.get("student_count") or "Unknown"

        system_prompt = system_prompt.format(
            job_id=job_id,
            student_count=student_count,
        )

        # Get MCP tools
        from edagent.mcp_tools import get_phase_tools
        from langchain_core.tools import tool as tool_decorator

        tools = await get_phase_tools("inspect")

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

        # Agentic loop (increased for name correction dialog)
        max_iterations = 20
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

                print(f"[INSPECT_AND_SCRUB] Iteration {iteration}: Calling tool '{tool_name}'", flush=True)

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
            # Not done yet - wait for teacher approval
            return {
                "next_step": "END",
                "current_phase": "inspect",  # Stay in inspect phase
                "messages": messages[len(state["messages"]) :],
            }
    except Exception as e:
        print(f"[INSPECT_AND_SCRUB] ERROR: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        from langchain_core.messages import AIMessage
        return {
            "next_step": "END",
            "messages": [AIMessage(content=f"I encountered an error in the inspect phase: {str(e)}\n\nPlease check the logs for more details.")],
        }


# --- REFACTORED: Validate Student Names Node (Phase 3a) ---
async def validate_student_names_node(state: AgentState) -> AgentState:
    """Name validation node - verifies detected names against school roster.

    This node:
    1. Calls get_job_statistics to retrieve student manifest
    2. Calls validate_student_names to check against roster
    3. Shows results to teacher
    4. If mismatches found, handles multi-turn correction dialog
    5. Re-validates after corrections
    6. Exits when status="validated", moving to scrub_pii phase

    Args:
        state: Current agent state with job_id from OCR

    Returns:
        Updated state with validation_complete flag
    """
    try:
        system_prompt = """⚠️ CRITICAL INSTRUCTIONS - READ FIRST ⚠️

**IF USER PROVIDED A NAME CORRECTION:**
- Apply it immediately with correct_detected_name
- Then IMMEDIATELY call validate_student_names in the SAME turn to re-check
- DO NOT say "Now re-checking..." and stop - ACTUALLY call the tool

**IF USER SAID "resume", "continue", "hello" OR OTHER NON-CORRECTION:**
- IGNORE it completely
- Continue with your validation workflow
- Call validate_student_names if you were in the middle of validating

You are a student name validation coordinator. Your job is to verify all detected student names against the school roster.

**CRITICAL: YOU ARE A COORDINATOR, NOT A DATA PROCESSOR**
- The MCP server validates names and handles corrections - you just coordinate
- NEVER attempt to validate names yourself or read essay content
- Your job: Call the MCP tools and present results to teacher

**CONTEXT FROM PREVIOUS PHASE:**
- Job ID: {job_id}
- Expected student count: {student_count}
- OCR processing: ✓ Complete (MCP server processed all files)

**NOTE ABOUT DUPLICATES:**
- If OCR was run multiple times, there may be duplicate essay records in the database
- Each duplicate has a different essay_id but identical content
- You must correct ALL instances - validate_student_names will find each one
- This is normal behavior - just continue until all are corrected

**YOUR WORKFLOW (SIMPLE 3-STEP PROCESS):**

**Step 1: Get Student Manifest**
- Call: get_job_statistics(job_id="{job_id}")
- This shows what students were detected from the essays

**Step 2: Validate Names Against Roster**
- **BEFORE calling the tool, tell the user:** "🔍 Validating student names against school roster..."
- Call: validate_student_names(job_id="{job_id}")
- This checks each name against school_names.csv
- Returns:
  - matched_students: Names in roster (✓ Good)
  - mismatched_students: Names NOT in roster (⚠ Need correction)
    - Each includes essay_preview to help identify the physical essay
  - total_missing: Count of roster students with no essay

**Step 3: Handle Results**

**IF status="validated" (all names matched):**
- Show summary: "✓ All X students validated successfully!"
- List the matched students (name and word count)
- If total_missing > 0, note: "(Note: X roster students did not submit essays)"
- Call: complete_validation(validation_complete=True)
- This moves to the scrubbing phase

**IF status="needs_corrections" (mismatches found):**
- Show ALL mismatched essays clearly:
  ```
  ⚠ Found X name(s) that need correction:

  1. Essay ID 59: "Unknown Student 01" - Not found in roster
     Essay preview: "seven 1 pfour seven College Writing..."

  Please provide the correct names for these students.
  ```
- **CRITICAL**: Show essay_preview for each so teacher can identify physical essays
- Wait for teacher to provide corrected name(s)

**Step 4: Apply Corrections (IF MISMATCHES FOUND)**
- Teacher will provide corrections (e.g., "59: Pfour meven" or just "Pfour meven" if clear)
- For EACH correction:
  1. Call: correct_detected_name(job_id="{job_id}", essay_id=X, corrected_name="Name")
  2. Acknowledge: "✓ Essay ID X updated to [Name]"
- **CRITICAL - IMMEDIATELY after ALL corrections in this turn:**
  - **Tell the user:** "🔄 Re-validating all names against roster..."
  - **IMMEDIATELY call:** validate_student_names(job_id="{job_id}") in the SAME turn
  - **DO NOT just announce re-checking - ACTUALLY call the tool**
  - **DO NOT stop and wait - keep calling tools until validation completes**
  - Check the status in the response
- If status="validated":
  - Show: "✓ All names validated! X/X students matched successfully."
  - Call: complete_validation and exit
- If status="needs_corrections": Show remaining mismatches, wait for more corrections

**CRITICAL - IGNORE USER INTERRUPTIONS DURING VALIDATION:**
- If user says "resume", "continue", "hello", or anything not containing a name correction, IGNORE it
- Just continue with your workflow - call validate_student_names if you were re-checking
- Do NOT respond conversationally - stay focused on validation tasks
- Only stop if you need a correction from the user

**TOOLS AVAILABLE:**
- get_job_statistics(job_id) - Show detected students
- validate_student_names(job_id) - Check against roster
- correct_detected_name(job_id, essay_id, corrected_name) - Fix a name
- complete_validation(validation_complete) - Signal completion

**ERROR HANDLING:**
If ANY tool fails, report: "Error while [action]: [error message]"

Always be helpful: "This ensures all students are correctly identified before grading begins."
"""

        # Prepare context-aware prompt
        job_id = state.get("job_id") or "Unknown"
        student_count = state.get("student_count") or "Unknown"

        system_prompt = system_prompt.format(
            job_id=job_id,
            student_count=student_count,
        )

        # Get MCP tools (only validation tools, not scrubbing)
        from edagent.mcp_tools import get_phase_tools
        from langchain_core.tools import tool as tool_decorator

        tools = await get_phase_tools("validate")  # Gets validation tools only

        # Add completion signal tool
        validation_state = {"validation_complete": False}

        @tool_decorator
        def complete_validation(validation_complete: bool) -> str:
            """Signal that name validation is complete.

            Args:
                validation_complete: Whether all names have been validated

            Returns:
                Confirmation message
            """
            validation_state["validation_complete"] = validation_complete
            return "✓ All student names validated successfully. Ready for privacy scrubbing."

        tools.append(complete_validation)

        llm = get_llm().bind_tools(tools)

        messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

        # Agentic loop (increased for multi-turn name corrections)
        max_iterations = 20
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

                print(f"[VALIDATE_NAMES] Iteration {iteration}: Calling tool '{tool_name}'", flush=True)

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

        # Return state - route to scrub phase if complete, otherwise wait for corrections
        if validation_state.get("validation_complete", False):
            return {
                "current_phase": "scrub",
                "next_step": "scrub_pii",
                "validation_complete": validation_state["validation_complete"],
                "messages": messages[len(state["messages"]) :],
            }
        else:
            # Not done yet - wait for teacher to provide corrections
            return {
                "next_step": "END",
                "current_phase": "validate",  # Stay in validate phase
                "messages": messages[len(state["messages"]) :],
            }
    except Exception as e:
        print(f"[VALIDATE_NAMES] ERROR: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        from langchain_core.messages import AIMessage
        return {
            "next_step": "END",
            "messages": [AIMessage(content=f"I encountered an error during name validation: {str(e)}\n\nPlease check the logs for more details.")],
        }


# --- REFACTORED: Scrub PII Node (Phase 3b) ---
async def scrub_pii_node(state: AgentState) -> AgentState:
    """PII scrubbing node - removes student names from essays for privacy.

    This node:
    1. Calls scrub_processed_job to remove PII from all essays
    2. Confirms completion
    3. Moves to evaluation phase

    This is a simple node that only runs after names are validated.

    Args:
        state: Current agent state with job_id and validated names

    Returns:
        Updated state with scrubbing_complete flag
    """
    try:
        system_prompt = """You are a privacy protection coordinator. Your job is to remove student names from essays before grading.

**CONTEXT:**
- Job ID: {job_id}
- Student names: ✓ Validated (all names confirmed correct)
- Ready to scrub PII for privacy-protected grading

**YOUR SIMPLE TASK:**

**Step 1: Remove PII from Essays**
- **BEFORE calling the tool, tell the user:** "🔒 Removing student names from essays for blind grading..."
- Call: scrub_processed_job(job_id="{job_id}")
- The MCP server removes all student names from essay text
- This ensures the AI grader doesn't see student identities
- **AFTER tool completes, confirm:** "✓ Privacy scrubbing complete. Essays are ready for evaluation."

**Step 2: Signal Completion**
- Call: complete_scrubbing(scrubbing_complete=True)
- This moves to the evaluation phase

**TOOLS AVAILABLE:**
- scrub_processed_job(job_id) - Remove PII from essays
- complete_scrubbing(scrubbing_complete) - Signal completion

That's it! This is a quick step before grading begins.
"""

        # Prepare context-aware prompt
        job_id = state.get("job_id") or "Unknown"

        system_prompt = system_prompt.format(job_id=job_id)

        # Get MCP tools
        from edagent.mcp_tools import get_phase_tools
        from langchain_core.tools import tool as tool_decorator

        tools = await get_phase_tools("scrub")  # Gets scrubbing tools only

        # Add completion signal tool
        scrubbing_state = {"scrubbing_complete": False}

        @tool_decorator
        def complete_scrubbing(scrubbing_complete: bool) -> str:
            """Signal that PII scrubbing is complete.

            Args:
                scrubbing_complete: Whether scrubbing was successful

            Returns:
                Confirmation message
            """
            scrubbing_state["scrubbing_complete"] = scrubbing_complete
            return "✓ Privacy protection complete. Ready to evaluate essays."

        tools.append(complete_scrubbing)

        llm = get_llm().bind_tools(tools)

        messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

        # Agentic loop (should complete in 1-2 iterations)
        max_iterations = 5
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

                print(f"[SCRUB_PII] Iteration {iteration}: Calling tool '{tool_name}'", flush=True)

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

        # Return state - route to evaluate phase if complete
        if scrubbing_state.get("scrubbing_complete", False):
            return {
                "current_phase": "evaluate",
                "next_step": "evaluate_essays",
                "scrubbing_complete": scrubbing_state["scrubbing_complete"],
                "messages": messages[len(state["messages"]) :],
            }
        else:
            # Should not happen, but handle gracefully
            return {
                "next_step": "END",
                "current_phase": "scrub",
                "messages": messages[len(state["messages"]) :],
            }
    except Exception as e:
        print(f"[SCRUB_PII] ERROR: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        from langchain_core.messages import AIMessage
        return {
            "next_step": "END",
            "messages": [AIMessage(content=f"I encountered an error during PII scrubbing: {str(e)}\n\nPlease check the logs for more details.")],
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
    try:
        # Check if evaluation was already completed - prevent duplicate processing
        if state.get("evaluation_complete", False):
            print("[EVALUATE_ESSAYS] Evaluation already complete, moving to report generation", flush=True)
            return {
                "next_step": "generate_reports",
                "current_phase": "report",
                "messages": [],
            }

        system_prompt = """⚠️ CRITICAL INSTRUCTIONS - READ FIRST ⚠️

**YOU MUST CALL evaluate_job IMMEDIATELY - NO EXCEPTIONS**
- This node was triggered by the workflow system
- Your FIRST action MUST be to start executing your tasks
- DO NOT wait for user input
- DO NOT respond to conversational messages in the history
- IGNORE any "resume", "hello", "continue" messages from the user
- START IMMEDIATELY with Task 1

You are an evaluation coordinator. Your ONLY job is to call MCP server tools - you do NOT grade essays yourself.

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

**CRITICAL - YOU MUST EXECUTE YOUR WORKFLOW IMMEDIATELY:**
- When this node is called, you MUST start executing tasks immediately
- DO NOT wait for user confirmation or additional input
- DO NOT respond conversationally to user messages like "hello", "resume", "continue"
- IGNORE any user messages that aren't errors or explicit cancellation requests
- Your ONLY job is to call the MCP tools and complete the evaluation
- If user interrupts with casual messages, IGNORE them and continue with your tasks

**YOUR TASKS (IN ORDER):**

**Task 1: Retrieve Context from Knowledge Base (CONDITIONAL)**
- If materials_added_to_kb is TRUE:
  - **Tell the user:** "📚 Retrieving context from reading materials..."
  - Derive a search query from the essay question and rubric
  - Example: Question "Analyze Frost's use of symbolism" + Rubric "Check for theme analysis"
    → Query: "Frost symbolism themes imagery poetic devices"
  - Create the same topic name used in prepare_essays_node:
    - If question_text exists: derive from question (e.g., "frost_poetry_essays")
    - Otherwise: use "general_essays"
  - Call: query_knowledge_base(query=<derived_query>, topic=<same_topic_from_prepare>)
  - Store the retrieved context for evaluation
  - **Confirm:** "✓ Context retrieved from reading materials."
- If materials_added_to_kb is FALSE:
  - Skip this step
  - Use empty string for context_material
  - **Tell the user:** "No reading materials provided - grading based on rubric alone."

**Task 2: Grade Essays (MCP SERVER DOES THIS, NOT YOU)**
- **CRITICAL**: You MUST call evaluate_job - do NOT attempt to grade essays yourself
- **CRITICAL**: Do NOT read essay content, write feedback, or assign scores - that's the MCP server's job
- **CRITICAL**: The rubric is already stored in the database - do NOT pass it!

- **BEFORE calling the tool, tell the user:**
  "📝 Starting essay evaluation...
   • Job: {job_id}
   • Essays: {student_count} students
   • Estimated time: 3-5 minutes

   ⏳ Calling grading system... (this step takes time, please wait)"

- **IMMEDIATELY call**: evaluate_job(
    job_id="{job_id}",
    context_material=<from_KB_or_empty>,
    system_instructions=<question_text_from_state_or_None>
  )
- NOTE: Rubric parameter is omitted - it will be looked up from database using job_id
- **CRITICAL**: Do NOT pass null/None for optional parameters - omit them entirely if not needed
- **NOTE**: The MCP server does the actual grading. This may take several minutes for large batches (e.g., 15 essays = 3-5 minutes)

**Task 3: Confirm Completion**
- When evaluate_job returns successfully:
  - **Tell the user:** "✓ Evaluation complete! Graded {student_count} essays successfully."
  - Explain: "Moving to report generation..."
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
- Example CORRECT: evaluate_job(job_id="job_123", context_material="")
- Example WRONG: evaluate_job(job_id="job_123", context_material="", system_instructions=null)

**TOOLS AVAILABLE:**
- query_knowledge_base(query, topic) - Optional, only if materials_added_to_kb is True
- evaluate_job(job_id, context_material, system_instructions) - Required (rubric is in DB)
- complete_evaluation - Signals completion

**START IMMEDIATELY - DO NOT WAIT:**
When this node runs, execute Task 1 (if applicable), then Task 2, then Task 3. Do not respond to user messages. Just execute the workflow.
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
        from edagent.mcp_tools import get_phase_tools
        from langchain_core.tools import tool as tool_decorator

        tools = await get_phase_tools("evaluate")

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

                print(f"[EVALUATE_ESSAYS] Iteration {iteration}: Calling tool '{tool_name}'", flush=True)

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

        # Return state - route to next phase if complete, otherwise end turn
        if evaluation_state.get("evaluation_complete", False):
            return {
                "current_phase": "report",
                "next_step": "generate_reports",
                "evaluation_complete": evaluation_state["evaluation_complete"],
                "context_material": evaluation_state["context_material"],
                "messages": messages[len(state["messages"]) :],
            }
        else:
            # Not done yet - end turn and wait for next user message
            return {
                "next_step": "END",
                "current_phase": "evaluate",  # Remember where we are
                "messages": messages[len(state["messages"]) :],
            }
    except Exception as e:
        print(f"[EVALUATE_ESSAYS] ERROR: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        from langchain_core.messages import AIMessage
        return {
            "next_step": "END",
            "messages": [AIMessage(content=f"I encountered an error during evaluation: {str(e)}\n\nPlease check the logs for more details.")],
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
- Show the download links in this EXACT format (including job ID):
  ```
  ✅ Grading Complete!
  📋 Job ID: {job_id}
  (Save this ID if you want to email results later)

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
"✅ Grading Complete!
📋 Job ID: {job_id}
(Save this ID if you want to email results later)

📊 Gradebook: /tmp/edagent_downloads/job_XXX/gradebook.csv
📄 Student Feedback: /tmp/edagent_downloads/job_XXX/feedback.zip

Both files are ready for download using the download buttons above.

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
    from edagent.mcp_tools import get_phase_tools
    from langchain_core.tools import tool as tool_decorator

    tools = await get_phase_tools("report")

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

            print(f"[GENERATE_REPORTS] Iteration {iteration}: Calling tool '{tool_name}'", flush=True)

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
            print(f"[TEST_GRADING] Iteration {iteration}: Calling tool '{tool_name}'", flush=True)

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

    system_prompt = f"""You are an email distribution coordinator. A grading job (job_id: {job_id_from_state}) has completed and reports are ready to email to students.

**CONTEXT:**
- Job ID: {job_id_from_state}
- Reports have been generated and are stored in the database
- Student emails are in the school roster CSV

**YOUR WORKFLOW (IN ORDER):**

**Task 1: Check Email Readiness**
- Call: identify_email_problems(job_id="{job_id_from_state}")
- This checks each student's name against the roster to find their email address
- Returns:
  - students_needing_help: Students who can't be emailed (name mismatch or no email)
  - ready_to_send: Count of students ready to email
  - status: "needs_corrections" or "ready"

**Task 2: Present Results to Teacher**
- If status="ready":
  - Show summary: "✓ All X students have valid emails. Ready to send!"
  - Ask: "Should I proceed with sending the emails?"
  - Wait for confirmation

- If status="needs_corrections":
  - Show the problem students clearly:
    ```
    ⚠ Found X student(s) with email issues:
    1. "Student Name" (Essay ID: 123) - Reason: Name not found in roster

    Options for this student:
    - Provide the correct name from your roster
    - Type "skip" to deliver manually (won't send email)
    ```
  - Wait for teacher to provide correction or skip

**Task 3: Fix Email Problems (IF NEEDED)**
- For each problem student, teacher will either:
  - Provide a corrected name → verify_student_name_correction(job_id, essay_id, suggested_name)
  - Say "skip" → skip_student_email(job_id, essay_id, reason="Teacher will deliver manually")
- The verify tool checks if the name is in the roster and has an email
- If verified, apply_student_name_correction(job_id, essay_id, confirmed_name)
- After ALL corrections, re-run identify_email_problems to confirm status="ready"

**Task 4: Send Emails**
Once status="ready" and teacher confirms:
- Call: send_student_feedback_emails(job_id="{job_id_from_state}")
- This sends emails to all students with valid email addresses
- Skips students marked for manual delivery
- Report results: "✓ Sent emails to X students. Y skipped for manual delivery."

**TOOLS AVAILABLE:**
- identify_email_problems(job_id)
- verify_student_name_correction(job_id, essay_id, suggested_name)
- apply_student_name_correction(job_id, essay_id, confirmed_name)
- skip_student_email(job_id, essay_id, reason)
- send_student_feedback_emails(job_id)

**IMPORTANT:**
- This workflow is generic - it works for essay grading, test grading, or any job with reports
- All student data comes from the database (any grading type stores essays/student_name/grade)
- Email addresses come from school_names.csv roster
- Be patient with the teacher - they may need time to look up correct names"""

    # Get email-specific tools from MCP server
    from edagent.mcp_tools import get_email_tools

    tools = await get_email_tools()

    llm = get_llm().bind_tools(tools)

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    # Agentic loop for email workflow (increased for multi-turn corrections)
    max_iterations = 30  # May need multiple rounds for teacher-in-the-loop name corrections
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
            print(f"[EMAIL_DISTRIBUTION] Iteration {iteration}: Calling tool '{tool_name}'", flush=True)

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
    next_step = state.get("next_step", "END")
    print(f"[ROUTE_DECISION] next_step={repr(next_step)}, current_phase={repr(state.get('current_phase'))}", flush=True)
    return next_step