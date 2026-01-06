# System Architecture Overview

**Purpose:** This document explains the complete EdAgent system architecture - both the AI agent and the EDMCP server - for onboarding developers or AI coding assistants.

---

## System Overview

EdAgent is a **two-component system** for educational grading automation:

1. **EdAgent (AI Agent)** - LangGraph-based conversational orchestrator
2. **EDMCP Server** - FastMCP tool server handling computational tasks

```
┌─────────────────────────────────────────────────────────────┐
│                        EdAgent                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │  Router  │───▶│  Essay   │    │   Test   │              │
│  │   Node   │    │ Grading  │    │ Grading  │              │
│  │ (STARTS  │    │   Node   │    │   Node   │              │
│  │  HERE!)  │    └──────────┘    └──────────┘              │
│  └──────────┘         │                │                    │
│       │               │                │                    │
│       └───────────────┴────────────────┘                    │
│                       │                                     │
│                  MCP Protocol                               │
│                  (stdio connection)                         │
└───────────────────────┼─────────────────────────────────────┘
                        │
┌───────────────────────┼─────────────────────────────────────┐
│                  EDMCP Server                               │
│  ┌───────────────────────────────────────────────────┐     │
│  │  17 Single-Purpose Tools                          │     │
│  │  • batch_process_documents  • scrub_processed_job │     │
│  │  • get_job_statistics       • evaluate_job        │     │
│  │  • add_to_knowledge_base    • query_knowledge_base│     │
│  │  • generate_gradebook       • generate_student_... │     │
│  └───────────────────────────────────────────────────┘     │
│  ┌───────────────────────────────────────────────────┐     │
│  │  Backend Services                                 │     │
│  │  • SQLite Database  • Qwen-VL OCR  • Vector Store │     │
│  └───────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

---

## Component 1: EdAgent (AI Agent)

### What It Is

**EdAgent** is a conversational AI orchestrator built with:
- **LangGraph** - State machine and routing logic
- **Chainlit** - Web-based chat interface
- **LangChain** - LLM integration (xAI Grok, OpenAI, or Anthropic)

**Location:** `/home/tcoop/Work/edagent/`

### Architecture: Router-First Design

**CRITICAL:** The agent **always starts with a router node**. User input never goes directly to specialist nodes.

#### Flow Diagram

```
User Message
     ↓
┌──────────────┐
│ Router Node  │ ← ENTRY POINT (always starts here)
└──────┬───────┘
       │
       │ Analyzes intent using LLM
       │ Returns: RouterDecision(next_step="gather_materials" | "test_grading" | "general" | "email_distribution")
       │
       ↓
┌──────────────────────────────────────┐
│  Conditional Routing                 │
│  (based on RouterDecision.next_step) │
└──────┬───────────────────────────────┘
       │
       ├──▶ 5-node Essay Grading Chain:
       │    gather_materials → prepare_essays → inspect_and_scrub
       │    → evaluate_essays → generate_reports → router
       │
       ├──▶ test_grading_node (has MCP tools)
       │
       ├──▶ email_distribution_node (has email tools)
       │
       └──▶ general_node (no tools, pure LLM chat)
```

#### Why Router-First?

1. **Intent classification** - Determines which specialist to engage
2. **Context preservation** - Can switch specialists mid-conversation
3. **Email routing** - Detects "email students" intent and routes appropriately
4. **Extensibility** - New specialists added by adding routes

### Key Components

#### State Management (`state.py`)

```python
class AgentState(TypedDict):
    # Core messaging and routing
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next_step: str  # Router decision: which node to execute next
    job_id: Optional[str]  # Carries job_id between grading → router → email

    # Workflow tracking (NEW)
    current_phase: Optional[str]  # "gather", "prepare", "inspect", "evaluate", "report"

    # Gathered materials (NEW)
    rubric_text: Optional[str]
    question_text: Optional[str]
    reading_materials_paths: Optional[List[str]]
    context_material: Optional[str]  # Retrieved from knowledge base

    # Progress flags (NEW)
    materials_added_to_kb: bool
    ocr_complete: bool
    scrubbing_complete: bool
    evaluation_complete: bool

    # Processing metadata (NEW)
    clean_directory_path: Optional[str]
    student_count: Optional[int]
    essay_format: Optional[str]  # "handwritten" or "typed"
```

**Hybrid State Management:**
- **Artifacts** (essays, evaluations, reports) stored in EDMCP database for persistence
- **Workflow state** (progress, materials, flags) tracked in AgentState for orchestration
- State is the ONLY way data flows between nodes - no global variables

#### Router Node (`nodes.py:58-159`)

```python
async def router_node(state: AgentState) -> AgentState:
    """
    Analyzes user intent and routes to appropriate specialist.

    Special case: If job_id exists in state AND user says email keywords,
    route directly to email_distribution (bypasses LLM routing).
    """
    # Check for pending email distribution
    job_id = state.get("job_id")
    last_message = state["messages"][-1].content.lower()
    email_keywords = ["email", "send", "yes", "ok"]

    if job_id and any(keyword in last_message for keyword in email_keywords):
        return {
            "next_step": "email_distribution",
            "job_id": job_id,  # Pass through
            "messages": [AIMessage(content="Let me send those emails...")]
        }

    # Otherwise, use LLM to decide
    llm = get_llm(with_structured_output=True)  # Returns RouterDecision
    decision = await llm.ainvoke(messages)

    return {
        "next_step": decision.next_step,
        "messages": [AIMessage(content="Routing to specialist...")]
    }
```

**Key:** Router uses **structured output** (Pydantic `RouterDecision` model) for reliable routing.

#### Specialist Nodes

Each specialist node:
1. **Has a custom system prompt** defining its expertise
2. **Has access to specific tools** (grading nodes get MCP tools)
3. **Runs an agentic loop** (calls tools iteratively until task complete)
4. **Returns state update** (next_step, job_id, messages, and phase-specific data)

**Essay Grading: 5-Node Checklist Pattern**

Essay grading is implemented as a **linear chain of 5 specialized nodes**, each handling one phase:

1. **`gather_materials_node`** (Phase 0 & 1)
   - Presents overview (first time only)
   - Asks for rubric, question, reading materials
   - Asks for essay format and student count
   - Updates: `rubric_text`, `question_text`, `reading_materials_paths`, `essay_format`, `student_count`
   - Routes to: `prepare_essays`

2. **`prepare_essays_node`** (Phase 2 & 3)
   - Asks for essay uploads
   - Calls `prepare_files_for_grading` (handles ZIP, images, PDFs)
   - Adds reading materials to knowledge base (if provided)
   - Calls `batch_process_documents` for OCR
   - Updates: `job_id`, `clean_directory_path`, `materials_added_to_kb`, `ocr_complete`
   - Routes to: `inspect_and_scrub`

3. **`inspect_and_scrub_node`** (Phase 4 & 5)
   - Calls `get_job_statistics` to show student manifest
   - Asks teacher: "Does this look correct?"
   - Calls `scrub_processed_job` to remove PII
   - Updates: `scrubbing_complete`
   - Routes to: `evaluate_essays`

4. **`evaluate_essays_node`** (Phase 6 & 7)
   - Queries knowledge base for context (if materials added)
   - Calls `evaluate_job` with rubric, context, and question
   - Updates: `evaluation_complete`, `context_material`
   - Routes to: `generate_reports`

5. **`generate_reports_node`** (Phase 8)
   - Calls `generate_gradebook` and `generate_student_feedback`
   - Calls `download_reports_locally` to get file paths
   - Shows download links to teacher
   - Asks: "Would you like to email these reports?"
   - **CRITICAL:** Calls `complete_grading_workflow(job_id, route_to_email=False)` to save job_id
   - Routes to: `router` (for email routing)

**Benefits of 5-Node Chain:**
- Each node ~100-150 lines (vs 645 lines monolithic)
- Clear responsibility separation
- Easy to modify individual phases
- State tracks progress through workflow
- Can resume from any phase
- Better error isolation

**Critical Pattern:** The final node (`generate_reports`) routes back to the router with `job_id` preserved in state, enabling email distribution routing.

#### MCP Tool Connection (`mcp_tools.py`)

```python
async def get_grading_tools():
    """Connects to EDMCP server via stdio and returns LangChain tools."""
    async with get_mcp_session() as session:
        # List tools from server
        tools_response = await session.list_tools()

        # Convert MCP tools to LangChain StructuredTools
        langchain_tools = []
        for tool in tools_response.tools:
            langchain_tools.append(convert_to_langchain_tool(tool))

        return langchain_tools
```

**How it works:**
1. Spawns EDMCP server as subprocess (stdio connection)
2. Lists available tools via MCP protocol
3. Converts to LangChain-compatible format
4. Returns tools for agent to use
5. Server stays alive for duration of tool calls

### LangGraph Workflow (`graph.py`)

```python
workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("router", router_node)  # ENTRY POINT

# 5-node essay grading chain
workflow.add_node("gather_materials", gather_materials_node)
workflow.add_node("prepare_essays", prepare_essays_node)
workflow.add_node("inspect_and_scrub", inspect_and_scrub_node)
workflow.add_node("evaluate_essays", evaluate_essays_node)
workflow.add_node("generate_reports", generate_reports_node)

# Other specialist nodes
workflow.add_node("test_grading", test_grading_node)
workflow.add_node("general", general_node)
workflow.add_node("email_distribution", email_distribution_node)

# Router is the entry point
workflow.set_entry_point("router")

# Conditional edges from router
workflow.add_conditional_edges(
    "router",
    route_decision,  # Looks at state["next_step"]
    {
        "gather_materials": "gather_materials",  # Start of essay chain
        "test_grading": "test_grading",
        "email_distribution": "email_distribution",
        "general": "general",
    }
)

# Linear chain for essay grading workflow
workflow.add_edge("gather_materials", "prepare_essays")
workflow.add_edge("prepare_essays", "inspect_and_scrub")
workflow.add_edge("inspect_and_scrub", "evaluate_essays")
workflow.add_edge("evaluate_essays", "generate_reports")
workflow.add_edge("generate_reports", "router")  # Back to router for email routing

# Test grading can route to email or end
workflow.add_conditional_edges(
    "test_grading",
    route_decision,
    {"email_distribution": "email_distribution", "END": END}
)

# General and email always end
workflow.add_edge("general", END)
workflow.add_edge("email_distribution", END)

# Compile with memory
app = workflow.compile(checkpointer=MemorySaver())
```

**Key Features:**
- Router is the **entry point** - all user messages flow through router first
- Essay grading uses a **linear chain** of 5 nodes for clear workflow progression
- `generate_reports` routes back to router to enable email distribution routing
- State is automatically persisted between messages via `MemorySaver`

---

## Component 2: EDMCP Server

### What It Is

**EDMCP** (Educational Document MCP) is a **FastMCP tool server** that provides:
- OCR processing (Qwen-VL vision model)
- Database state management (SQLite)
- RAG knowledge base (vector store)
- Report generation (CSV, PDFs)
- Email distribution (SMTP via Brevo)

**Location:** `/home/tcoop/Work/edmcp/`

### Core Philosophy: One Tool, One Job

**Every tool does exactly ONE thing.** No multi-purpose Swiss Army knife tools.

#### Good Example: ✅

```python
@mcp.tool
def batch_process_documents(directory_path: str, job_name: str) -> dict:
    """Process PDFs with OCR. ONLY does OCR. Returns job_id."""
    # Does: OCR, name detection, text extraction
    # Doesn't: Scrubbing, evaluation, reporting
    # Returns: job_id for next steps

@mcp.tool
def scrub_processed_job(job_id: str) -> dict:
    """Remove student names. ONLY does scrubbing."""
    # Does: PII removal
    # Doesn't: OCR, evaluation
    # Returns: confirmation

@mcp.tool
def evaluate_job(job_id: str, rubric: str, context_material: str) -> dict:
    """Grade essays. ONLY does grading."""
    # Does: AI evaluation
    # Doesn't: OCR, scrubbing, reporting
    # Returns: confirmation
```

#### Bad Example: ❌

```python
@mcp.tool
def process_and_grade_everything(files, rubric, send_emails=False):
    """Does everything in one mega-function."""
    # OCR + scrubbing + evaluation + reporting + email = BAD!
    # Can't reuse parts, can't debug, can't test independently
```

### Available Tools (17 Total)

#### Document Processing
- `batch_process_documents(directory_path, job_name)` - OCR multiple PDFs
- `extract_text_from_image(image_path)` - OCR single image
- `convert_pdf_to_text(pdf_path, use_ocr)` - Extract/OCR single PDF

#### File Utilities
- `prepare_files_for_grading(file_paths)` - Convert images to PDF, validate files
- `convert_image_to_pdf(image_path, output_path)` - Image → PDF conversion
- `read_text_file(file_path)` - Read text files

#### Job Management
- `get_job_statistics(job_id)` - Get student manifest
- `scrub_processed_job(job_id)` - Remove PII
- `normalize_processed_job(job_id, format)` - Fix OCR errors (optional)

#### Evaluation
- `evaluate_job(job_id, rubric, context_material, system_instructions)` - Grade essays
- `generate_gradebook(job_id)` - Create CSV gradebook
- `generate_student_feedback(job_id)` - Create individual PDF reports
- `download_reports_locally(job_id)` - Download reports from DB to temp files

#### Knowledge Base (RAG)
- `add_to_knowledge_base(file_paths, topic)` - Ingest reading materials
- `query_knowledge_base(query, topic)` - Retrieve context for grading

#### Email Distribution
- `send_student_feedback_emails(job_id)` - Send feedback PDFs via email

#### Archive Management
- `cleanup_old_jobs(days_to_keep)` - Delete jobs older than N days
- `export_job_archive(job_id, output_path)` - Export job for compliance

### Backend Services

#### SQLite Database (`edmcp/core/db.py`)

**Schema:**
```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,           -- "job_20260103_182412_99612336"
    created_at TEXT,
    status TEXT,
    name TEXT                      -- "WR121_Essays"
);

CREATE TABLE essays (
    id INTEGER PRIMARY KEY,
    job_id TEXT,                   -- Foreign key to jobs
    student_name TEXT,
    raw_text TEXT,                 -- OCR output
    scrubbed_text TEXT,            -- PII removed
    normalized_text TEXT,          -- OCR errors fixed (optional)
    evaluation TEXT,               -- JSON feedback
    grade TEXT,                    -- "18/20"
    status TEXT,                   -- PENDING → SCRUBBED → EVALUATED
    metadata TEXT                  -- JSON (page count, word count, etc.)
);

CREATE TABLE reports (
    id INTEGER PRIMARY KEY,
    job_id TEXT,
    report_type TEXT,              -- "gradebook", "student_feedback", "feedback_zip"
    essay_id INTEGER,              -- NULL for gradebook/zip
    filename TEXT,
    content BLOB,                  -- PDF or CSV content
    created_at TEXT
);
```

**Key Pattern:** All operations reference `job_id`. Agent passes job_id between tools.

#### OCR Engine (`edmcp/core/ocr.py`)

- Uses **Qwen2-VL-7B-Instruct** vision model
- Runs on local GPU (CUDA) or CPU fallback
- Detects student names from "Name: John Doe" pattern
- Extracts full essay text
- Returns structured JSON per page

#### Vector Store (`edmcp/core/knowledge_base.py`)

- Uses **Chroma** for vector storage
- Embeds reading materials per topic
- Supports multi-document queries
- Isolated by topic (prevents cross-contamination)

#### Report Generator (`edmcp/core/report_generator.py`)

- Creates CSV gradebooks with columns: Student Name, Grade, Feedback Summary
- Creates individual PDF feedback reports (ReportLab)
- Stores reports in database (reports table)
- Can download to temp files for teacher access

#### Email Sender (`edmcp/core/email_sender.py`)

- SMTP integration (Brevo/Sendinblue)
- Jinja2 templates for email body
- Attaches PDF reports
- Idempotent (tracks sent emails, won't double-send)
- Fuzzy name matching (handles OCR errors like "Pfour Six" → "P4 Six")

---

## How They Work Together

### Communication Protocol: MCP (Model Context Protocol)

**MCP** is a standardized protocol for connecting AI assistants to tools.

```
┌──────────────┐                    ┌──────────────┐
│  EdAgent     │◀──── stdio ──────▶│ EDMCP Server │
│  (Client)    │                    │  (Server)    │
└──────────────┘                    └──────────────┘
```

**Communication:**
1. Agent spawns server as subprocess
2. Communicates via **stdin/stdout** (stdio transport)
3. Uses JSON-RPC protocol
4. Server responds with structured JSON

**Example Tool Call:**

```
Agent: "I need to OCR these essays"
  ↓
Agent calls: batch_process_documents(directory_path="/tmp/essays", job_name="WR121_Essays")
  ↓
MCP Client (mcp_tools.py) sends JSON-RPC request:
  {
    "method": "tools/call",
    "params": {
      "name": "batch_process_documents",
      "arguments": {
        "directory_path": "/tmp/essays",
        "job_name": "WR121_Essays"
      }
    }
  }
  ↓
EDMCP Server (server.py) executes tool
  ↓
Returns JSON response:
  {
    "job_id": "job_20260103_182412_99612336",
    "total_documents": 25,
    "students_detected": 23,
    "unknown_students": 2
  }
  ↓
Agent receives result and continues workflow
```

### Data Flow Example: Complete Grading Workflow

```
1. USER: "I need to grade 25 essays"
   ↓
2. ROUTER NODE: Routes to essay_grading (intent classification)
   ↓
3. ESSAY GRADING NODE: Asks for rubric
   ↓
4. USER: Uploads rubric file
   ↓
5. ESSAY GRADING NODE: Asks for essays
   ↓
6. USER: Uploads ZIP file with 25 PDFs
   ↓
7. ESSAY GRADING NODE: Calls prepare_files_for_grading(file_paths=[...])
   EDMCP: Validates files, converts images to PDF, returns clean directory
   ↓
8. ESSAY GRADING NODE: Calls batch_process_documents(directory_path=...)
   EDMCP:
     - OCR with Qwen-VL
     - Detect student names
     - Store in database
     - Return job_id
   ↓
9. ESSAY GRADING NODE: Calls get_job_statistics(job_id)
   EDMCP: Return student manifest
   Agent: Shows to teacher for verification
   ↓
10. USER: "Looks good, proceed"
    ↓
11. ESSAY GRADING NODE: Calls scrub_processed_job(job_id)
    EDMCP: Remove all student names from text
    ↓
12. ESSAY GRADING NODE: Calls evaluate_job(job_id, rubric, context_material="")
    EDMCP:
      - For each student:
        - Read scrubbed text
        - Apply rubric with LLM
        - Store evaluation + grade in database
    ↓
13. ESSAY GRADING NODE: Calls generate_gradebook(job_id)
    EDMCP: Create CSV from database, store in reports table
    ↓
14. ESSAY GRADING NODE: Calls generate_student_feedback(job_id)
    EDMCP: Create 25 PDFs, store in reports table
    ↓
15. ESSAY GRADING NODE: Calls download_reports_locally(job_id)
    EDMCP: Download CSV + ZIP from database to temp files
    ↓
16. ESSAY GRADING NODE: Returns state with job_id
    ↓
17. ESSAY GRADING NODE: Asks "Would you like to email these reports?"
    AND calls complete_grading_workflow(job_id, route_to_email=False)
    ↓
18. USER: "Yes, email students"
    ↓
19. ROUTER NODE: Detects job_id + email keywords → routes to email_distribution
    ↓
20. EMAIL DISTRIBUTION NODE: Calls send_student_feedback_emails(job_id)
    EDMCP:
      - For each student:
        - Fuzzy match name to roster
        - Retrieve PDF from database
        - Send email via SMTP
        - Log result
    ↓
21. EMAIL DISTRIBUTION NODE: Reports results
    ↓
22. END
```

**Notice:** Agent orchestrates, server executes. Clean separation of concerns.

---

## Design Philosophy

### 1. Single Responsibility Principle (Tools)

**Every tool does ONE thing well.**

- ✅ `batch_process_documents` - ONLY OCR
- ✅ `scrub_processed_job` - ONLY PII removal
- ✅ `evaluate_job` - ONLY grading
- ✅ `generate_gradebook` - ONLY CSV creation

**Why?**
- **Composability** - Tools can be combined in different workflows
- **Testability** - Each tool can be tested independently
- **Debuggability** - Easy to identify where failures occur
- **Reusability** - Same tool works for essays, tests, lab reports

### 2. State-Based Workflow (Agent)

**All data flows through state.** No global variables, no hidden state.

```python
# Node returns state update
return {
    "next_step": "email_distribution",
    "job_id": "job_123",
    "messages": [AIMessage(content="...")]
}
```

**Benefits:**
- **Predictable** - State transitions are explicit
- **Debuggable** - Can inspect state at any point
- **Resumable** - Can save/restore conversations (MemorySaver)

### 3. Job-Based Architecture (Server)

**Every grading task gets a unique job_id.** All operations reference it.

```python
job_id = batch_process_documents(...)  # Creates job
scrub_processed_job(job_id)            # Operates on job
evaluate_job(job_id, ...)              # Operates on job
generate_gradebook(job_id)             # Reads from job
```

**Benefits:**
- **No data passing** - Server manages state in database
- **Idempotent** - Can retry operations safely
- **Auditable** - All operations logged per job

### 4. Router-First Architecture (Agent)

**User input ALWAYS goes to router first.**

**Why?**
- **Intent classification** - Route to correct specialist
- **Context switching** - Can change specialists mid-conversation
- **Email detection** - Special routing for email distribution
- **Extensibility** - New specialists = new routes

---

## File Structure

### EdAgent (`/home/tcoop/Work/edagent/`)

```
edagent/
├── edagent/
│   ├── app.py              # Chainlit UI entry point
│   ├── graph.py            # LangGraph workflow definition
│   ├── nodes.py            # Router + specialist nodes
│   ├── state.py            # AgentState schema
│   ├── mcp_tools.py        # MCP client connection
│   └── file_utils.py       # ZIP extraction, PDF organization
├── main.py                 # CLI entry point
├── pyproject.toml          # Dependencies (uv)
├── .env                    # API keys, MCP server path
└── *.md                    # Documentation
```

### EDMCP Server (`/home/tcoop/Work/edmcp/`)

```
edmcp/
├── server.py               # FastMCP server with 17 tools
├── edmcp/
│   ├── core/
│   │   ├── db.py           # SQLite database manager
│   │   ├── ocr.py          # Qwen-VL OCR engine
│   │   ├── knowledge_base.py  # Chroma vector store
│   │   ├── report_generator.py  # CSV + PDF generation
│   │   ├── email_sender.py      # SMTP + templates
│   │   └── student_roster.py    # Name matching, fuzzy search
│   ├── tools/
│   │   ├── emailer.py      # Email distribution orchestration
│   │   └── name_fixer.py   # Name correction workflow (deprecated)
│   └── data/
│       ├── names/
│       │   └── school_names.csv  # Student roster
│       ├── email_templates/      # Jinja2 templates
│       └── reports/              # Job output directories
├── edmcp.db                # SQLite database
├── pyproject.toml          # Dependencies
└── .env                    # SMTP credentials
```

---

## Testing & Development

### Testing EdAgent

```bash
cd /home/tcoop/Work/edagent
source .venv/bin/activate

# Run with Chainlit UI
chainlit run edagent/app.py

# Test MCP connection
python -c "from edagent.mcp_tools import get_grading_tools; import asyncio; asyncio.run(get_grading_tools())"
```

### Testing EDMCP Server

```bash
cd /home/tcoop/Work/edmcp
source .venv/bin/activate

# Test tool directly
python -c "from server import batch_process_documents; print(batch_process_documents('/path/to/pdfs', 'Test Job'))"

# Test email workflow
python test_email_workflow.py  # Custom test script
```

### Debug Outputs

Add these to track data flow:

```python
# In router_node
print(f"[ROUTER] State: next_step={state.get('next_step')}, job_id={state.get('job_id')}")

# In grading nodes
print(f"[GRADING] Calling tool: {tool_name} with args: {tool_args}")

# In EDMCP server
print(f"[EDMCP] Executing {tool_name} for job_id={job_id}")
```

---

## Key Takeaways for AI Coding Assistants

When working on this codebase:

### ✅ DO:
1. **Understand the router-first architecture** - User input → Router → Specialist
2. **Respect single-responsibility** - One tool = one job, one node = one phase
3. **Use state for data flow** - Pass job_id, next_step, and workflow data through state
4. **Follow the 5-node essay grading chain** - gather → prepare → inspect → evaluate → generate_reports
5. **Understand hybrid state management** - Artifacts in DB (persistent), workflow in state (ephemeral)
6. **Check both repos** - Bug could be in agent (edagent) OR server (edmcp)

### ❌ DON'T:
1. **Skip the router** - All input goes through router first
2. **Create mega-tools** - Keep tools focused and single-purpose
3. **Use global state** - Everything flows through AgentState
4. **Bypass the database** - Server manages all grading data via job_id
5. **Assume tools auto-chain** - Agent orchestrates, tools execute

---

## Summary

**EdAgent System = Conversational Orchestrator + Computational Backend**

- **Agent** (edagent) - Routes, guides, orchestrates via 5-node essay grading chain
- **Server** (edmcp) - Processes, stores, generates
- **Communication** - MCP protocol over stdio
- **Philosophy** - One tool one job; router-first; hybrid state (DB + agent); linear workflow chains
- **Essay Grading** - 5 specialized nodes (gather → prepare → inspect → evaluate → generate_reports)
- **Result** - Scalable, testable, maintainable grading automation with clear phase separation

**Start here:** Router node (`nodes.py:58`) → Understand routing → Explore specialist nodes → See how they call EDMCP tools → Trace data flow through state.

---

**Last Updated:** January 3, 2026
**For Questions:** Refer to README.md (setup), PROJECT_PHILOSOPHY.md (UX principles), WORKFLOW_EMAIL_INTEGRATION.md (email routing)
