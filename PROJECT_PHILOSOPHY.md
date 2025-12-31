# EdAgent Project Philosophy & Current State

## Philosophy: Teacher-First AI Assistant

### Core Principles

#### 1. **Conversational, Not Transactional**
Teachers interact with EdAgent like they would with a helpful teaching assistant, not a command-line tool or form to fill out.

- **Ask ONE question at a time** - No overwhelming lists of requirements
- **Natural language understanding** - No rigid commands or keywords
- **Context-aware** - Remember the conversation, adapt to answers
- **Encouraging and patient** - Teachers may not be tech-savvy

**Example:**
```
❌ BAD: "Please provide: 1. Essays (directory path), 2. Rubric (file path), 
         3. Test question, 4. Reading materials (paths)"

✅ GOOD: "Are the essays handwritten or typed?"
        [Wait for answer]
        "Was there a specific question for this assignment?"
        [Wait for answer]
```

#### 2. **Discovery Before Execution**
Understand the teacher's context before asking for materials or running operations.

- **What type of assignment?** (Essay test, open-topic paper, quiz)
- **What materials exist?** (Question, rubric, reading materials, lecture notes)
- **What's the format?** (Handwritten vs typed, PDF vs scanned images)
- **What's the scale?** (5 students vs 150 students)

This discovery phase determines:
- Which tools to use
- Whether RAG (knowledge base) is needed
- What quality checks to perform
- How to set teacher expectations

#### 3. **Human-in-the-Loop, Not Black Box**
Teachers need control and visibility at critical decision points.

**Checkpoint: Student Detection**
```
Agent: "I found 23 students:
        - John Smith (3 pages, 450 words)
        - Jane Doe (2 pages, 380 words)
        - Unknown Student 01 (4 pages, 520 words)
        
Does this look correct? Are all your students accounted for?"

Teacher can verify before proceeding to evaluation.
```

**Why?**
- Builds trust (teacher sees what the AI sees)
- Catches errors early (missing students, merged documents)
- Gives teacher control (proceed or retry)

#### 4. **File Uploads, Not Directory Paths**
Teachers work with files, not terminal commands.

**❌ DON'T:**
```
"Please provide the directory path: /home/username/Documents/..."
```

**✅ DO:**
```
"You can upload your essays using the 📎 button - either as individual 
PDFs or a ZIP file containing all essays."
```

**Implementation:**
- Accept multiple PDFs via Chainlit's file picker
- Accept ZIP files and extract automatically
- Create temporary directories behind the scenes
- Never expose technical paths to teachers

#### 5. **Heavy-Lifter Pattern: Server Does the Bulk Work**
The MCP server handles computationally expensive operations (OCR, evaluation) and maintains state in a database. The agent orchestrates the workflow and provides guidance.

**Agent's Role:**
- Gather context and materials
- Guide teacher through workflow
- Call appropriate MCP tools
- Present results in teacher-friendly format

**Server's Role:**
- OCR processing (Qwen-VL)
- PII scrubbing (regex + name lists)
- Database state management (SQLite)
- RAG knowledge base (vector store)
- Evaluation (AI-powered grading)
- Report generation (CSV, PDFs)

**Communication:**
- Agent passes `job_id` between operations
- Server returns summaries, not massive text blobs
- Keeps agent context clean and focused

#### 6. **Specialized Nodes for Different Tasks**
Don't create one "grading" node that tries to do everything.

**Current Specialists:**
- **Router Node**: Analyzes intent, routes to appropriate specialist
- **Essay Grading Node**: Handles written essays with qualitative feedback
- **Test Grading Node**: Handles objective tests with answer keys
- **General Node**: Handles non-grading questions

**Why Specialization?**
- Essays need rubric application, writing quality analysis, argument evaluation
- Tests need answer key matching, objective scoring, partial credit
- Different workflows, different tools, different evaluation criteria

#### 7. **Graceful Degradation: Work With What You Have**
Not every grading scenario includes all materials. The system adapts.

**Scenario 1: Full Context (Ideal)**
- Essays ✓
- Rubric ✓
- Test question ✓
- Reading materials ✓
- **Result:** RAG-enhanced, context-aware grading

**Scenario 2: Minimal Context**
- Essays ✓
- Rubric ✓
- **Result:** Rubric-only grading (still effective)

**Scenario 3: Single File Test**
- One essay ✓
- Quick rubric ✓
- **Result:** Immediate feedback for testing the system

The agent adjusts its workflow based on what materials are available.

---

## Where We Are: Current Development State

### ✅ **Phase 1: Foundation (COMPLETE)**

**Router-Expert Architecture**
- ✅ Router node analyzes intent using structured output (Pydantic models)
- ✅ Routes to essay grading, test grading, or general assistance
- ✅ LangGraph state management with MemorySaver (conversation persistence)
- ✅ Chainlit UI with file attachment support

**MCP Server Connection**
- ✅ FastMCP server running at `/home/tcoop/Work/edmcp`
- ✅ 17 tools exposed (OCR, scrubbing, evaluation, RAG, reporting, archive)
- ✅ Connection via stdio protocol
- ✅ All tools accessible to agent nodes

**File Handling Utilities**
- ✅ `extract_zip_to_temp` - Extract ZIP files
- ✅ `organize_pdfs_to_temp` - Organize multiple PDFs
- ✅ `read_text_file` - Read rubrics/prompts
- ✅ `list_directory_files` - Verify contents

### ✅ **Phase 2: Essay Grading Workflow Design (COMPLETE)**

**Discovery Phase (Phase 0)**
- ✅ Ask about handwritten vs typed
- ✅ Ask about test question/prompt
- ✅ Ask about reading materials
- ✅ Ask about number of students
- ✅ ONE question at a time approach

**Material Gathering (Phase 1)**
- ✅ Essay uploads (PDF or ZIP)
- ✅ Rubric (upload or paste)

**Optional Materials (Phase 2)**
- ✅ Reading materials for RAG
- ✅ Lecture notes
- ✅ Test question

**Pipeline Execution (Phases 3-7)**
- ✅ System prompt includes complete workflow
- ✅ OCR processing step
- ✅ Human inspection checkpoint
- ✅ Privacy protection (scrubbing)
- ✅ Knowledge base integration (RAG)
- ✅ Evaluation step
- ✅ Report generation step

### 🚧 **Phase 3: Testing & Refinement (IN PROGRESS)**

**What's Working:**
- ✅ Router correctly identifies essay vs test grading
- ✅ Essay node starts with discovery questions (not assumptions)
- ✅ Conversational flow with memory
- ✅ Tools are loaded and accessible

**What Needs Testing:**
1. ⏳ **End-to-end workflow execution**
   - Upload PDFs → OCR → Inspect → Scrub → Evaluate → Reports
   - Test with real sample essays

2. ⏳ **File handling in practice**
   - ZIP extraction
   - Multiple PDF organization
   - Rubric reading from files

3. ⏳ **Human checkpoint interaction**
   - Presenting statistics to teacher
   - Handling "yes proceed" vs "no retry" responses

4. ⏳ **Knowledge base (RAG) workflow**
   - Ingesting reading materials
   - Querying for context
   - Injecting context into evaluation

5. ⏳ **Error handling**
   - No students detected (all "Unknown Student")
   - Evaluation fails (API timeout, model issues)
   - Missing materials (rubric not uploaded)

6. ⏳ **Report delivery**
   - Providing file paths for download
   - Explaining what each file contains

### ❌ **Phase 4: Additional Nodes (NOT STARTED)**

**Test Grading Node**
- Similar structure to essay grading
- Different evaluation approach (answer key matching)
- Objective scoring vs qualitative feedback
- Bubble sheet support

**Curriculum Node** (Low Priority)
- Currently exists but needs updating
- Lesson plan design
- Learning objectives
- No MCP tools needed (pure LLM work)

**Maintenance Node** (Future)
- `cleanup_old_jobs` (210 day retention)
- `delete_knowledge_topic`
- `search_past_jobs` (discovery)
- `export_job_archive` (legal compliance)

### 📋 **Immediate Next Steps**

1. **Test essay grading end-to-end** with sample data
   - 3-5 sample PDFs with "Name: Student A" format
   - Simple rubric
   - Walk through discovery → upload → processing → reports

2. **Handle edge cases**
   - Student detection failures
   - Partial file uploads
   - Tool execution errors

3. **Improve progress indicators**
   - "Processing essays... this may take a few minutes"
   - Progress updates during long operations

4. **Refine checkpoint interactions**
   - Better formatting of statistics
   - Clearer "yes/no" prompts

5. **Add error recovery flows**
   - "It looks like no students were detected. Would you like me to explain the name format requirements?"

---

## Design Patterns in Action

### Pattern 1: The Discovery-Gather-Execute Loop

```
Discovery (Context Questions)
    ↓
Gather Materials (Files, Rubrics)
    ↓
Execute Pipeline (OCR → Scrub → Evaluate)
    ↓
Checkpoint (Human Verification)
    ↓
Continue or Retry
    ↓
Deliver Results
```

### Pattern 2: Job-Based State Management

```
Teacher Request
    ↓
batch_process_documents() → Returns job_id
    ↓
get_job_statistics(job_id) → Returns manifest
    ↓
scrub_processed_job(job_id) → Updates DB
    ↓
evaluate_job(job_id, ...) → Updates DB
    ↓
generate_gradebook(job_id) → Reads DB, creates CSV
```

All operations reference the same `job_id`. No passing of massive text blobs.

### Pattern 3: Conditional RAG (Agent as Bridge)

```
IF reading materials provided:
    add_to_knowledge_base(files, topic)
    context = query_knowledge_base(query, topic)
    evaluate_job(job_id, rubric, context_material=context)
ELSE:
    evaluate_job(job_id, rubric, context_material="")
```

The agent decides whether to use RAG based on discovery phase answers.

### Pattern 4: Teacher-Friendly Error Messages

```
❌ Technical: "FileNotFoundError: /tmp/essays/student_01.pdf"

✅ Friendly: "I couldn't find some of the essay files. This might happen if 
             the ZIP file was corrupted during upload. Would you like to 
             try uploading again?"
```

---

## Success Metrics

How do we know when EdAgent is working well?

1. **Teacher Confidence**
   - Teachers feel understood and guided
   - They know what to expect at each step
   - They trust the results

2. **Minimal Back-and-Forth**
   - Discovery questions catch requirements upfront
   - Fewer "I meant..." corrections needed
   - Smooth progression through workflow

3. **Successful Grading**
   - Students correctly detected (verified at checkpoint)
   - Grades align with rubric expectations
   - Feedback is constructive and specific

4. **Time Savings**
   - Batch grading 40 essays in < 30 minutes (vs hours manually)
   - Reports ready for immediate distribution
   - No manual CSV creation needed

5. **Repeat Usage**
   - Teachers come back for next assignment
   - They recommend it to colleagues
   - They explore advanced features (RAG, archives)

---

## Open Questions & Future Considerations

### Technical

1. **Should normalization be automatic or opt-in?**
   - Current: Agent doesn't mention it unless OCR quality is poor
   - Alternative: Always run, but transparently

2. **How to handle very large batches (150+ students)?**
   - Progress indicators?
   - Batch processing in chunks?
   - Estimated time remaining?

3. **Iterative refinement support?**
   - "The grades seem too harsh, can you re-evaluate with more leniency?"
   - Would require re-running evaluate_job with modified instructions

### UX

1. **Should we show raw job_id to teachers?**
   - Current: Mostly hidden, only used internally
   - Alternative: Show for reference ("Job #12345")

2. **How to handle ambiguous materials?**
   - Teacher uploads 5 PDFs - are they all essays? Or 4 essays + 1 rubric?
   - Agent should ask: "I see 5 PDFs. Are these all student essays?"

3. **Undo/retry operations?**
   - "Actually, I want to change the rubric and re-grade"
   - Would need new evaluate_job call with same job_id

### Compliance

1. **Data retention notifications?**
   - "Your essays will be stored for 7 months for grade dispute purposes"
   - Automatic cleanup after 210 days

2. **Export for legal purposes?**
   - `export_job_archive(job_id)` creates chain-of-custody ZIP
   - When/how to offer this to teachers?

---

## Team Onboarding

If someone new joins the project, they should:

1. **Read this document** (philosophy & current state)
2. **Read `DEVELOPMENT_NOTES.md`** (technical details, testing checklist)
3. **Read `REDESIGN_PLAN.md`** (detailed workflow specifications)
4. **Read `CONVERSATION_FLOW_FIX.md`** (example of our iterative improvement process)
5. **Review `/home/tcoop/Work/edmcp/README.md`** (MCP server capabilities)
6. **Test the agent** with sample data (walk through essay grading flow)
7. **Read `edagent/nodes.py`** (where all agent behavior is defined)

---

## Closing Thoughts

EdAgent is being built with **teachers as the primary users**, not developers. Every design decision prioritizes:

- **Clarity** over technical precision
- **Guidance** over autonomy
- **Trust** over black-box automation
- **Conversation** over commands

We're building a **teaching assistant**, not a grading script. The difference matters.

---

**Last Updated:** December 30, 2024  
**Current Phase:** Testing & Refinement  
**Next Milestone:** End-to-end essay grading workflow validated with real data
