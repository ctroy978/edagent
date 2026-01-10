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

**IMPORTANT - How to handle optional parameters:**
- If question_text was provided: Include it as a string
- If question_text was NOT provided: OMIT the parameter entirely (do not pass "None" as a string!)

**Example when question AND context are provided:**
```
create_job_with_materials(
    job_name="WR121_Essays",
    rubric="<the rubric text>",
    question_text="Analyze themes in Frost's poetry",
    knowledge_base_topic="WR121_Essays"  # Topic used when adding to knowledge base
)
```

**Example when only rubric is provided:**
```
create_job_with_materials(
    job_name="WR121_Essays",
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
                                print(f"[GATHER_MATERIALS] Context materials added to knowledge base. Topic: {topic}", flush=True)
                            else:
                                print(f"[GATHER_MATERIALS] ERROR: add_to_knowledge_base failed: {result_dict}", flush=True)
                        except Exception as e:
                            print(f"[GATHER_MATERIALS] ERROR: Failed to parse add_to_knowledge_base result: {e}", flush=True)
                            print(f"[GATHER_MATERIALS] Raw result: {result}", flush=True)

                    # Special handling for create_job_with_materials - extract job_id
                    if tool_name == "create_job_with_materials":
                        import json
                        try:
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

Always be encouraging: "Great! Essays are processed. Let's verify the student list next..."
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
                "clean_directory_path": preparation_state["clean_directory_path"],
                "ocr_complete": preparation_state["ocr_complete"],
                "messages": messages[len(state["messages"]) :],
            }
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

**YOUR TASKS (IN ORDER):**

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
  - missing_students: Roster students with no essay found

**Task 3: Present Results to Teacher**
- If ALL names validated (status="validated"):
  - Show summary: "✓ All X students validated successfully!"
  - List the matched students
  - If there are missing students, note: "⚠ These roster students didn't submit: [list]"
  - Ask: "Ready to proceed with scrubbing?"

- If MISMATCHES found (status="needs_corrections"):
  - Show the problem:
    ```
    ⚠ Found X name(s) that need correction:
    1. "pfour seven" (Essay ID: 5) - Not found in roster

    This is likely an OCR error. What is the correct name for this student?
    ```
  - Wait for teacher to provide the corrected name
  - Do NOT proceed to scrubbing until all names are corrected

**Task 4: Correct Mismatched Names (IF NEEDED)**
- For EACH mismatched student, the teacher will provide a corrected name
- Call: correct_detected_name(job_id="{job_id}", essay_id=<id>, corrected_name="<teacher_provided_name>")
- The tool will:
  - Verify the corrected name exists in the roster
  - Update the database
  - Show a reminder to update school_names.csv if needed
- After ALL corrections are made, re-run validate_student_names to confirm
- Once status="validated", proceed to Task 5

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
- **CRITICAL**: The rubric is already stored in the database - do NOT pass it!
- Call: evaluate_job(
    job_id="{job_id}",
    context_material=<from_KB_or_empty>,
    system_instructions=<question_text_from_state_or_None>
  )
- NOTE: Rubric parameter is omitted - it will be looked up from database using job_id
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
- Example CORRECT: evaluate_job(job_id="job_123", context_material="")
- Example WRONG: evaluate_job(job_id="job_123", context_material="", system_instructions=null)

**TOOLS AVAILABLE:**
- query_knowledge_base(query, topic) - Optional, only if materials_added_to_kb is True
- evaluate_job(job_id, context_material, system_instructions) - Required (rubric is in DB)
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