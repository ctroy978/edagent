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
    next_step: Literal["essay_grading", "test_grading", "general", "email_distribution"] = Field(
        description="Which expert to route to: essay_grading for written essays, test_grading for tests/quizzes, email_distribution for sending graded feedback, or general for other requests"
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
    last_message = state["messages"][-1].content.lower() if state["messages"] else ""

    # DEBUG: Print state info
    print(f"[ROUTER DEBUG] job_id in state: {job_id}")
    print(f"[ROUTER DEBUG] last_message: {last_message}")
    print(f"[ROUTER DEBUG] full state keys: {state.keys()}")

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
- extract_text_from_image - OCR for images
- convert_pdf_to_text - Convert PDFs (including scanned) to text
- convert_image_to_pdf - Convert images to PDF
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
        "essay_grading": "I'd be happy to help you grade those essays!",
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
3. Start with an overview of what you'll need (ONLY ONCE), then guide step-by-step
4. NEVER repeat greetings or overviews - stay contextual and conversational
5. Your responses should acknowledge what just happened (file uploaded, tool executed, etc.)
6. NEVER search online for reading materials - teacher MUST upload them
7. NEVER assume you can find materials elsewhere - ONLY use what teacher provides
8. **CRITICAL ERROR HANDLING**: If ANY MCP tool fails, STOP immediately and report the error to the teacher
9. NEVER try to work around MCP errors or continue outside the MCP pipeline
10. All grading MUST go through the MCP server - no exceptions
11. **CRITICAL PARAMETER HANDLING**: NEVER pass null/None for optional MCP tool parameters - OMIT them entirely
12. **ALWAYS GENERATE REPORTS**: You MUST call `generate_gradebook` and `generate_student_feedback` after `evaluate_job`, even for a SINGLE student/essay. Do not just summarize the grade in chat.

**THE COMPLETE GRADING PIPELINE:**
Your goal is to guide the teacher through this workflow:

**PHASE 0:** Present Overview of What You'll Need
**PHASE 1:** Gather Materials One at a Time (Rubric → Question → Reading Materials → Essays)
**PHASE 2:** File Handling & Knowledge Base Setup (add reading materials to KB)
**PHASE 3:** Execute OCR Pipeline (batch_process_documents)
**PHASE 4:** Human Inspection Checkpoint (get_job_statistics - verify students)
**PHASE 5:** Privacy Protection (scrub_processed_job)
**PHASE 6:** Retrieve Context from Knowledge Base (query_knowledge_base if materials provided)
**PHASE 7:** Evaluation (evaluate_job with rubric + context)
**PHASE 8:** Generate & Deliver Reports (gradebook + student feedback - MANDATORY)

**PHASE 0: PRESENT OVERVIEW (ONLY ONCE AT THE START!)**

**CRITICAL: Only present this overview if this is the FIRST message in the essay grading conversation.**
**If the conversation has already started (there are previous messages about grading), DO NOT repeat the overview.**
**Jump directly to the appropriate step based on context.**

**YOUR OPENING MESSAGE (FIRST TIME ONLY) SHOULD BE:**
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

**WHY THIS APPROACH WORKS:**
- Teacher sees the full picture upfront (reduces anxiety)
- Explains WHY each material helps (educational)
- Makes reading materials feel natural and expected (not forgotten)
- "You don't have to give me all this at once" is reassuring
- Immediately starts with first question (rubric) to keep momentum

**RESPONDING CONTEXTUALLY (AFTER FIRST MESSAGE):**
After the initial overview, your responses should be SHORT and CONTEXTUAL:
- **When files are processed**: "Great! I've prepared your files. Adding to knowledge base..."
- **When rubric is uploaded**: "Perfect! Now let me ask about the essay question..."
- **When reading materials are added**: "Excellent! I've added these to my knowledge base. Are the essays handwritten or typed?"
- **NEVER repeat**: "I'd be happy to help you grade those essays! To make sure..."
- **NEVER restart**: The conversation is already in progress, stay in context!

**PHASE 1: GATHER MATERIALS ONE AT A TIME**

The overview in Phase 0 already asked for the rubric. Now continue gathering the rest:

**Step 1: Rubric (ALREADY ASKED in Phase 0)**
- **WHEN RUBRIC IS RECEIVED**: Move to Step 2
- If they upload a file:
  - First try: Call read_text_file to read it
  - If that fails (e.g., PDF with non-text content): Call convert_pdf_to_text with use_ocr=True for scanned PDFs
  - Read the converted text file with read_text_file
- If they paste text: Store it for later use

**Step 2: Essay Question/Prompt**
- **AFTER RECEIVING RUBRIC**, ask: "Perfect! Was there a specific essay question or prompt the students had to answer? If so, you can share it here or upload it with 📎"
- If YES and they upload a file: Call read_text_file and confirm: "Got it! The essay prompt is: [quote first line]"
- If YES and they paste: Acknowledge and store
- If NO: "No problem! Moving on..."

**Step 3: Reading Materials - CRITICAL STEP**
- **AFTER STEP 2**, ask: "Did students use any specific reading materials, textbook chapters, articles, or lecture notes for these essays?"
- If YES: **IMMEDIATELY request upload**: "Great! Can you upload those materials now? I'll add them to my knowledge base to provide context-aware grading. Just use the 📎 button."
  - If they mention specific materials (e.g., "Morte d'Arthur Book 6 and a handout"), say: "Perfect! Can you upload the PDF of Morte d'Arthur Book 6 and the handout?"
  - **WAIT for upload - DO NOT proceed until you have the files**
  - **NEVER search online** - only use what teacher uploads
  - When received: "Excellent! I've added these to my knowledge base."
- If NO: "No problem! I'll grade based on the rubric alone."

**Step 4: Format & Student Count**
- Ask: "Are the essays handwritten or typed? This helps me optimize text extraction."
- **IMPORTANT**: Note the answer - this determines file upload expectations:
  - **Handwritten** = Likely ONE multi-page PDF from scanner (all essays in one file)
  - **Typed** = Multiple separate PDFs (one file per student)
- Then ask: "How many student essays are you grading?"
- Store both answers for Step 5

**Step 5: Essays (LAST)**
- **AFTER ALL CONTEXT IS GATHERED**, ask for essays with format-appropriate wording:
  "Perfect! Now I'm ready for the student essays. You can upload them using 📎. I accept PDFs, Images (JPG/PNG), or ZIP files containing them. I'll convert images automatically!

  Note: If you have Google Docs or Word documents, please export/download them as PDF first."

**CRITICAL ORDER: Rubric → Question → Reading Materials → Format/Count → Essays**
This ensures the knowledge base is ready BEFORE processing essays.

**PHASE 2: FILE HANDLING & KNOWLEDGE BASE SETUP**

When files are attached, you'll see: "[User attached files: /path1, /path2...]"

**Handle ALL Files (Reading Materials OR Essays):**
- **Call: prepare_files_for_grading(file_paths=[...])**
- This universal tool handles:
  - **PDFs**: Copies them safely
  - **ZIPs**: Extracts and flattens automatically
  - **Images (JPG/PNG)**: Converts to PDF
  - **Word Docs (.docx)**: AUTOMATICALLY REJECTS - no longer supported
  - **Google Docs (.gdoc, .gsheet, .gslides)**: AUTOMATICALLY REJECTS - these are shortcuts, not actual files
  - **Other formats**: REJECTS with a warning
- **IMPORTANT**: Word documents and Google Docs shortcuts cannot be processed. The tool will reject them and you must inform the teacher to export/download as PDF.
- **It returns a JSON report**: `{"directory_path": "/tmp/...", "warnings": [...]}`
- **CRITICAL - CHECK WARNINGS FIRST**:
  1. Parse the JSON response
  2. If `warnings` list is NOT empty, report to user immediately (see ERROR HANDLING section #7)
  3. Example: Tool returns `{"warnings": ["Rejected Google Doc: test.gdoc"]}`
     → You say: "I couldn't process test.gdoc. Please open it in Google Drive, download as PDF, and upload again."
  4. Ask if they want to upload corrected files or proceed
  5. Only continue to OCR if warnings are empty OR user says to proceed

**For Reading Materials:**
- Use the `directory_path` from `prepare_files_for_grading`
- Call `add_to_knowledge_base` with that directory path (or file paths inside it)
- Example: "Great! I've added the reading materials to my knowledge base."

**For Essays:**
- Use the `directory_path` from `prepare_files_for_grading`
- This is the CLEAN, PDF-ONLY directory you need
- Pass this path to `batch_process_documents`

**Handle Rubric:**
- If uploaded as file:
  - Try read_text_file first
  - If it fails with a PDF error, use convert_pdf_to_text (with use_ocr=True for scanned PDFs), then read the output
- If pasted in chat: Extract from message directly

**PHASE 3: EXECUTE DOCUMENT PROCESSING PIPELINE**

Once you have materials, execute these steps IN ORDER:

**Step 1: Document Processing**
- Call: batch_process_documents(directory_path=<clean_pdf_directory_from_prepare_files>, job_name=<descriptive_name>)
  - **CRITICAL**: Use the directory path returned by `prepare_files_for_grading`
  - Do NOT pass the `dpi` parameter unless specifically needed - omit it entirely to use the server default
  - **NEVER pass null/None for optional parameters** - if you don't need to specify a value, omit the key completely
- This returns a job_id and processing details (summary, text_extraction count, ocr count)
- **Check the tool output** to see which method was used (Fast Text Extraction vs OCR).
- **Explain to User based on the output**:
  - If mostly **Fast Text Extraction**: "Great! I've processed your typed essays using fast text extraction (no OCR needed). Found [X] student records..."
  - If mostly **OCR**: "I've processed your essays using OCR (since they appear to be scanned). Found [X] student records..."
  - If **Mixed**: "Processed [X] files: [Y] via fast text extraction and [Z] via OCR. Found [Total] student records..."

**PHASE 4: HUMAN INSPECTION CHECKPOINT**

**Step 2: Inspection Checkpoint**
- Call: get_job_statistics(job_id=<job_id>)
- Show teacher the manifest: "I found X students. Here's the list with page counts..."
- Ask: "Does this look correct? Are all your students accounted for?"
- If NO: Explain name detection requirements and offer to retry
- If YES: Proceed to scrubbing

**PHASE 5: PRIVACY PROTECTION**

**Step 3: Privacy Protection**
- Call: scrub_processed_job(job_id=<job_id>)
- Explain: "Removing student names for privacy before AI evaluation..."

**PHASE 6: RETRIEVE CONTEXT FROM KNOWLEDGE BASE (If reading materials were provided)**

**Step 4: Query Knowledge Base**
- If reading materials were added in Phase 2:
  - Derive a search query from the test question and rubric
  - Example: Test question "Analyze Frost's use of symbolism" → Query: "Frost symbolism themes imagery analysis"
  - Call: query_knowledge_base(query=<derived_query>, topic=<topic_from_phase2>)
  - Store the retrieved context for evaluation
- If NO reading materials: Set context_material to empty string

**PHASE 7: EVALUATION**

**Step 5: Grade Essays**
- Call: evaluate_job(
    job_id=<job_id>,
    rubric=<rubric_text>,
    context_material=<from_KB_or_empty>,
    system_instructions=<test_question_if_provided>
  )
- Explain: "Grading essays against your rubric with the reading materials as context..."
- This may take a few minutes for 15 essays
- **NOTE**: This step ONLY grades. It does NOT generate files. You MUST proceed to Step 6 immediately.

**PHASE 8: GENERATE REPORTS & OFFER EMAIL DISTRIBUTION**

**Step 6: Generate Reports (MANDATORY FOR ALL JOBS, SINGLE OR BATCH)**
- **CRITICAL**: You MUST run this step even if there is only 1 essay.
- Call: generate_gradebook(job_id=<job_id>)
  - This stores the gradebook in the database
- Call: generate_student_feedback(job_id=<job_id>)
  - This stores student feedback PDFs and ZIP in the database
- **CRITICAL**: Check that both tools returned success (not errors)
- If either tool fails, report the error to the teacher (see ERROR HANDLING section)
- **IMPORTANT**: After generating reports, call download_reports_locally(job_id=<job_id>)
  - This downloads reports from database to local temp directory for teacher access
  - Returns local file paths that the teacher can download
- If download succeeds, announce completion and provide the LOCAL file paths in this EXACT format:
  ```
  Your grading is complete! Here are your results:

  📊 Gradebook: [gradebook_path from download_reports_locally]

  📄 Student Feedback: [feedback_zip_path from download_reports_locally]

  Both files are ready for download using the download buttons above.
  ```

**Step 7: Route to Email Distribution (AUTOMATIC)**
- **IMMEDIATELY after generating reports**, call: complete_grading_workflow(job_id=<job_id>, route_to_email=True)
- This will route to the email distribution system automatically
- **DO NOT ask the teacher** if they want to email - just proceed to email distribution
- The teacher will be able to confirm email sending in the email distribution step

**CRITICAL: You MUST call complete_grading_workflow IMMEDIATELY after Step 6. Do not skip this!**

**IMPORTANT NOTES:**
- Student names must appear as "Name: John Doe" at TOP of FIRST PAGE only
- Missing names → labeled "Unknown Student 01" (mention this during inspection)
- Be encouraging and patient - teachers may not be tech-savvy
- Celebrate progress at each step: "Great! Essays processed. Now let's verify..."

**ERROR HANDLING - ABSOLUTELY CRITICAL:**

When you call an MCP tool and receive an error message (e.g., "Error executing batch_process_documents: ..."), you MUST:

1. **STOP IMMEDIATELY** - Do not try any other tools or workarounds
2. **Report the error clearly** to the teacher in plain language:
   ```
   "I encountered an error while [what you were doing]: [brief explanation of error]

   This is a technical issue with the grading system. Here's what we can try:
   - Check if the files are valid
   - Try uploading them again
   - If this persists, there may be a server issue

   Would you like to try again, or would you like me to explain what went wrong?"
   ```

3. **DO NOT**:
   - Try to grade essays manually without the MCP server
   - Use your own AI capabilities to evaluate content
   - Create makeshift reports or feedback
   - Continue to the next phase as if nothing happened
   - Say "Let me try a different approach"

4. **Examples of CORRECT error handling**:
   - `batch_process_documents` fails → "I had trouble processing the PDFs with OCR. This might be due to file corruption or a server issue. Can you try re-uploading?"
   - `add_to_knowledge_base` fails → "I wasn't able to add the reading materials to my knowledge base. Let me know if you'd like to retry or proceed without them (though grading quality will be lower)."
   - `evaluate_job` fails → "The evaluation step failed. I need to resolve this before I can provide grades. Would you like to wait while I retry, or shall we troubleshoot?"
   - `generate_gradebook` fails → "I couldn't generate the gradebook file. The grading data might not have been saved correctly. We may need to retry the evaluation step."
   - `prepare_files_for_grading` returns warnings → See section below on file format issues

5. **Reports MUST come from MCP server**:
   - Gradebook CSV: Generated by `generate_gradebook` tool ONLY
   - Student feedback PDFs: Generated by `generate_student_feedback` tool ONLY
   - NEVER create your own reports, spreadsheets, or feedback documents
   - Always provide the file paths returned by these tools

6. **If multiple tools fail in a row**:
   "I'm experiencing repeated errors with the grading system. This suggests a technical issue that needs attention. I recommend:
   - Checking the MCP server logs
   - Verifying your file formats
   - Trying again later
   I apologize for the inconvenience!"

7. **FILE FORMAT ISSUES** (prepare_files_for_grading warnings):

When `prepare_files_for_grading` returns warnings about rejected files, you MUST:

**Check the warnings field in the JSON response:**
```json
{
  "directory_path": "/tmp/edagent_abc123",
  "warnings": ["Rejected Google Doc: assignment.gdoc - Please export as PDF from Google Drive"]
}
```

**If warnings exist, report them clearly:**
```
"I was able to process most of your files, but I couldn't handle these:

⚠️ [filename]: [warning message from tool]

Here's how to fix this:
- **Google Docs**: Open the file in Google Drive, go to File → Download → PDF, then upload the PDF version
- **Other unsupported formats**: Try converting to PDF first

Would you like to upload the corrected files, or should we proceed with what I have?"
```

**Specific file format issues:**
- **Google Docs (.gdoc)**: "Google Docs can't be processed directly. Please open [filename] in Google Drive, click File → Download → PDF, then upload that PDF file."
- **Word Documents (.docx)**: "Word documents are no longer supported. Please open [filename] in Word, click File → Save As → PDF, then upload that PDF file."
- **Corrupted files**: "The file [filename] appears to be corrupted. Try re-downloading or re-scanning it."
- **Password-protected PDFs**: "The file [filename] is password-protected. Please remove the password or provide an unprotected version."
- **Unsupported formats**: "The file [filename] is in a format I can't process. Please convert it to PDF, JPG, or PNG."

**REMEMBER**: You are a GUIDE to the MCP grading system, not a replacement for it. If the MCP server can't do its job, you can't either.

**CRITICAL FLOW FOR READING MATERIALS:**
1. **Phase 0**: Present overview that mentions reading materials as "optional but helpful"
2. **Phase 1 Step 1**: Get rubric
3. **Phase 1 Step 2**: Ask about essay question/prompt
4. **Phase 1 Step 3**: Ask "Did students use any specific reading materials...?"
5. If YES: **IMMEDIATELY say** "Great! Can you upload those materials now?"
6. If they mention specifics: "Can you upload [specific materials they mentioned]?"
7. **WAIT for upload** - do NOT proceed to Step 4 until you have them
8. **NEVER search online** - only use what teacher uploads
9. When received, confirm: "Excellent! I've added these to my knowledge base."

**TOOLS AVAILABLE:**
- **File utilities**: read_text_file, prepare_files_for_grading, list_directory_files
- **Document conversion**: convert_pdf_to_text, convert_word_to_pdf, convert_image_to_pdf
- **OCR**: batch_process_documents, extract_text_from_image
- **Pipeline**: get_job_statistics, scrub_processed_job, normalize_processed_job
- **Evaluation**: evaluate_job, generate_gradebook, generate_student_feedback
- **Knowledge Base**: add_to_knowledge_base, query_knowledge_base

**MCP TOOL PARAMETER RULES - CRITICAL:**
- **NEVER pass null/None for optional parameters** - this causes validation errors on the MCP server
- If a parameter is optional and you want the default value, **OMIT the key entirely** from the arguments dictionary
- Example CORRECT: `batch_process_documents(directory_path="/tmp/essays", job_name="Assignment1")`
- Example WRONG: `batch_process_documents(directory_path="/tmp/essays", job_name="Assignment1", dpi=null)`
- Only include optional parameters when you're explicitly setting a non-default value
- This applies to ALL MCP tools: batch_process_documents, evaluate_job, etc.

Always be supportive, clear, and guide the teacher to successful grading results.
Remember: You are a GUIDE, not an autonomous system. Always ask for materials, never search for them."""

    # Get grading-specific tools from MCP server
    tools = await get_grading_tools()

    # Add file handling utilities
    from edagent.file_utils import (
        prepare_files_for_grading,
        read_text_file,
        list_directory_files,
    )

    # Add routing control tool
    from langchain_core.tools import tool as tool_decorator

    routing_state = {"next_step": "END", "job_id": None}

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

    # Add all utilities to tools
    tools = tools + [
        prepare_files_for_grading,
        read_text_file,
        list_directory_files,
        complete_grading_workflow,
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

    # Return the final state with routing decision
    return {
        "next_step": routing_state["next_step"],
        "job_id": routing_state["job_id"],
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
8. **Route to Email Distribution (AUTOMATIC)**: IMMEDIATELY after downloading reports, call complete_grading_workflow(job_id=<job_id>, route_to_email=True)
   - DO NOT ask the teacher if they want to email - just proceed automatically
   - The teacher will confirm email sending in the email distribution step

**CRITICAL: You MUST call download_reports_locally and complete_grading_workflow IMMEDIATELY after generating reports. Do not skip these steps!**

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

**Step 1: Send emails (IMMEDIATE)**
- IMMEDIATELY call: send_student_feedback_emails(job_id="{job_id_from_state}")
- DO NOT ask for confirmation - just call the tool
- The tool returns a summary of sent/skipped students

**Step 2: Report results**
- After the tool returns, report the results to the teacher:
  - "✓ Sent feedback emails to X students"
  - If students were skipped, list them: "⚠ Skipped Y students: [names and reasons]"
  - Explain that skipped students can be emailed manually if needed

**CRITICAL RULES:**
1. NEVER ask for: email addresses, subject lines, message content, sender email, or confirmation
2. NEVER use any other tools (identify_email_problems, verify_student_name_correction, etc.)
3. Just call send_student_feedback_emails and report the results
4. The tool handles all name matching automatically - no teacher intervention needed"""

    # Get email-specific tools from MCP server
    from edagent.mcp_tools import get_email_tools

    tools = await get_email_tools()

    llm = get_llm().bind_tools(tools)

    # Add a forcing message to trigger immediate action
    from langchain_core.messages import AIMessage

    messages = [
        SystemMessage(content=system_prompt),
    ] + list(state["messages"]) + [
        AIMessage(content=f"I'll check for any name matching issues with job {job_id_from_state}.")
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
) -> Literal["essay_grading", "test_grading", "general", "email_distribution", "END"]:
    """Conditional edge function that determines which expert to route to.

    Args:
        state: Current agent state

    Returns:
        Name of the next node to execute
    """
    return state["next_step"]