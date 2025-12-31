# EdAgent Redesign Plan - Full EDMCP Integration

## Current Status

### What We Have
- ✅ Router node that correctly identifies essay vs test grading intent
- ✅ Basic conversational scaffolding
- ✅ File attachment support in Chainlit
- ✅ MCP tools connection via stdio
- ✅ LangGraph memory with thread IDs

### What We're Missing
- ❌ Complete grading pipeline execution
- ❌ File handling (ZIP extraction, PDF organization)
- ❌ Human-in-the-loop inspection checkpoints
- ❌ Knowledge base (RAG) integration
- ❌ Report generation and delivery
- ❌ All 17 EDMCP tools properly exposed

---

## EDMCP Server Capabilities (17 Tools)

### Document Processing
1. `batch_process_documents` - OCR PDFs, detect student names, save to DB
2. `process_pdf_document` - Single file OCR
3. `extract_text_from_image` - Image OCR
4. `get_job_statistics` - Manifest for inspection

### Privacy & Quality
5. `scrub_processed_job` - PII redaction
6. `normalize_processed_job` - AI-powered OCR error correction (optional)

### Evaluation
7. `evaluate_job` - Grade essays with rubric + context

### Knowledge Base (RAG)
8. `add_to_knowledge_base` - Ingest reference materials
9. `query_knowledge_base` - Retrieve context chunks

### Reporting
10. `generate_gradebook` - CSV with grades
11. `generate_student_feedback` - Individual PDF reports (zipped)

### Archive & Discovery
12. `search_past_jobs` - Full-text search
13. `export_job_archive` - ZIP export with chain-of-custody

### Maintenance
14. `cleanup_old_jobs` - Delete old data (210 day retention)
15. `delete_knowledge_topic` - Remove KB topics

---

## Essay Grading Scenarios

### Scenario 1: Full Context Essay (Most Common)
**Teacher Has:**
- Handwritten essays (scanned PDFs)
- Grading rubric
- Test question/prompt
- Reading materials (textbook chapters, articles)
- Lecture notes

**Workflow:**
1. Upload essays (ZIP or multiple PDFs)
2. Upload rubric
3. Provide test question
4. Upload reading materials
5. Pipeline: OCR → Inspect → Scrub → KB Ingest → Query KB → Evaluate → Reports

### Scenario 2: Minimal Context Essay
**Teacher Has:**
- Typed essays (saved as PDFs)
- Grading rubric only (students chose their own topics)

**Workflow:**
1. Upload essays
2. Upload/paste rubric
3. Pipeline: OCR → Inspect → Scrub → Evaluate (no KB) → Reports

### Scenario 3: Single File Quick Grade
**Teacher Has:**
- One essay to test the system
- Quick rubric

**Workflow:**
1. Upload single PDF
2. Paste rubric
3. Pipeline: process_pdf_document → Evaluate → Immediate feedback

---

## Redesigned Essay Grading Node

### Phase 1: Material Gathering (Conversational)

**REQUIRED:**
- Q1: "Do you have essays ready to grade? You can upload them using the 📎 button - either as individual PDF files or a ZIP file containing all essays."
- Q2: "Do you have a grading rubric? You can upload it or paste it here."

**OPTIONAL (Ask one at a time):**
- Q3: "Was there a specific question or prompt for this essay?"
- Q4: "Do you have reading materials the students used? (textbook chapters, articles, etc.)"
- Q5: "Do you have lecture notes or slides to reference?"

### Phase 2: File Processing

**Step 1: Handle Uploads**
- Detect ZIP → `extract_zip_to_temp`
- Detect multiple PDFs → `organize_pdfs_to_temp`
- Detect rubric file → `read_text_file` or parse from chat
- Detect reading materials → Note paths for KB

**Step 2: OCR Processing**
```
Call: batch_process_documents(
    directory_path=<temp_folder>,
    job_name=<descriptive_name_from_context>
)
Returns: job_id
```

Message: "I'm processing the essays with OCR. This will detect student names and extract all text..."

### Phase 3: Human-in-the-Loop Checkpoint

**Step 3: Inspection**
```
Call: get_job_statistics(job_id)
Returns: manifest with student names, page counts, word counts
```

Show teacher:
```
I found X students:
- Student A (3 pages, 450 words)
- Student B (2 pages, 380 words)
- Unknown Student 01 (4 pages, 520 words)
...

Does this look correct? Are all your students accounted for?
```

**If NO:** Explain name detection requirements:
- "Name: John Doe" must be at TOP of FIRST PAGE
- Later pages auto-group
- Can retry with corrections

**If YES:** Proceed

### Phase 4: Privacy & Quality

**Step 4: Scrubbing**
```
Call: scrub_processed_job(job_id)
```

Message: "Removing student names to protect privacy before AI evaluation..."

**Step 5: Optional Normalization** (Only if teacher requests or OCR quality is poor)
```
Call: normalize_processed_job(job_id)
```

### Phase 5: Knowledge Base (If Materials Provided)

**Step 6: KB Ingestion**
```
IF reading materials were provided:
    Call: add_to_knowledge_base(
        file_paths=[<reading_materials>],
        topic=<descriptive_topic_from_context>
    )
```

**Step 7: Context Retrieval**
```
Call: query_knowledge_base(
    query=<derived_from_test_question_and_rubric>,
    topic=<topic>
)
Returns: context_text
```

Agent must intelligently derive search queries from:
- Test question: "Analyze Frost's use of symbolism"
- → Query: "Frost symbolism analysis themes Mending Wall Road Not Taken"

### Phase 6: Evaluation

**Step 8: Grade Essays**
```
Call: evaluate_job(
    job_id=<job_id>,
    rubric=<rubric_text>,
    context_material=<from_KB_or_empty_string>,
    system_instructions=<test_question_if_provided>
)
```

Message: "Grading essays against your rubric... This may take a few minutes..."

### Phase 7: Report Generation

**Step 9: Generate Reports**
```
Call: generate_gradebook(job_id)
Returns: csv_path

Call: generate_student_feedback(job_id)
Returns: zip_path with individual PDFs
```

**Step 10: Deliver Results**
```
Message:
"Your grading is complete! Here's what I've prepared:

📊 Gradebook: [csv_path]
   - Contains all student grades in spreadsheet format

📄 Individual Feedback: [zip_path]
   - Detailed feedback PDFs for each student
   - Ready to return to students

You can download both files now."
```

---

## File Upload Strategy

### Problem
- Chainlit doesn't support folder uploads natively
- Teachers may not understand "directory paths"

### Solution (Implemented)
1. **Accept ZIP files**: Teacher zips their essays folder
   - Agent calls `extract_zip_to_temp` → returns temp directory path
   
2. **Accept multiple PDFs**: Teacher selects all PDFs in file picker
   - Agent calls `organize_pdfs_to_temp` → copies to temp directory

3. **File categorization**: Automatically detect file types
   - PDFs → essays
   - TXT/MD → rubrics, prompts
   - Additional PDFs → reading materials

---

## Implementation Status

### Completed
- ✅ Updated `get_grading_tools()` to include all 17 tools
- ✅ Created `file_utils.py` with ZIP/PDF handling
- ✅ Redesigned essay grading node system prompt with full workflow
- ✅ Added file handling tools to essay grading node

### Next Steps
1. **Test the complete workflow** with real data
2. **Handle edge cases**:
   - What if no students detected?
   - What if evaluation fails?
   - What if KB query returns no results?
3. **Add progress indicators** for long operations
4. **Build test grading node** with similar structure
5. **Add maintenance node** for cleanup operations
6. **Improve error messages** for teacher-friendly language

---

## Testing Checklist

### Manual Test Scenario
1. Start agent: `uv run chainlit run edagent/app.py`
2. Say: "I have essays to grade"
3. Upload 3-5 sample PDFs with "Name: Student A" headers
4. Upload or paste a simple rubric
5. Optionally provide test question
6. Follow agent prompts through:
   - Inspection checkpoint
   - Scrubbing confirmation
   - Evaluation
   - Report download

### Expected Outcome
- Job ID created
- Statistics shown (verify student count)
- Scrubbing completes
- Evaluation generates JSON feedback
- Gradebook CSV created
- Individual PDFs generated

---

## Design Principles Maintained

✅ **Natural Language Understanding** - No rigid commands
✅ **Conversational Flow** - One question at a time
✅ **Human-in-the-Loop** - Inspection checkpoint before proceeding
✅ **No Internal Thinking** - Clean responses only
✅ **Teacher-Friendly** - No technical jargon, no directory paths
✅ **Heavy-Lifter Pattern** - Server does bulk work, returns summaries
✅ **Job-Based State** - All operations reference job_id

---

## Open Questions

1. **Should normalization be automatic or opt-in?**
   - Current: Opt-in (teacher requests if needed)
   - Alternative: Auto-run if OCR confidence is low

2. **How to handle missing context materials gracefully?**
   - Current: Evaluate without context (rubric-only grading)
   - Alternative: Warn that grading quality may be reduced

3. **Should we support iterative refinement?**
   - e.g., "The grades seem too harsh, can you re-evaluate with more leniency?"
   - Would require re-calling `evaluate_job` with modified instructions

4. **Archive old jobs automatically?**
   - Add a background task or agent-initiated maintenance?

---

## Next Session Goals

1. Test the updated essay grading node end-to-end
2. Add error handling and recovery flows
3. Implement test grading node (similar structure)
4. Add curriculum node updates if needed
5. Create demo video for user testing
