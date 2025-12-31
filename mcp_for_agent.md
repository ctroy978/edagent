# EDMCP: Educational Data MCP Server - Agent Integration Guide


For this project, we will be creating an AI agent using langgraph. This agent will interface with the user using chainlit. The purpose of the agent is to assist teacher in using the edmcp suite of tools. These tools are described below. In short, the agent will guide users in using ai to evaluate different types of essays and tests using a variety of tools to assist the process. The tools we will use are described below.   

## Overview
This document provides comprehensive technical details for developers building AI agents that interact with the **EDMCP (Educational Data Model Context Protocol)** server. 

The server is designed to act as a **"Heavy Lifter"** for batch processing student documents (PDFs). Instead of flooding the agent's context with raw file data, the server processes files locally and returns structured, referenced data (JSONL), allowing the agent to handle high-volume tasks (e.g., "Grade these 40 essays") efficiently.


## Core Architecture: The Job Pipeline

The server operates on a **Job-based** architecture to manage state and data flow.

1.  **Initiation**: Agent calls a batch tool (e.g., `batch_process_documents`).
2.  **Processing**: Server processes files (OCR, Scrubbing) and assigns a unique `job_id`.
3.  **Storage**: Results are streamed to a local JSONL file identified by the `job_id`.
4.  **Handoff**: The tool returns a lightweight summary to the agent, including the `job_id` and the absolute path to the output file.
5.  **Consumption**: The agent (or downstream tools) reads the JSONL file to perform analysis (Grading, Feedback).

---

## Available Tools

### 1. Batch OCR Processing
**Tool Name:** `batch_process_documents`

**Purpose:** 
Primary entry point for ingesting a directory of student PDFs. It performs OCR using Qwen-VL (via OpenAI-compatible API) and aggregates pages into student records.

**Parameters:**
- `directory_path` (string, required): Absolute path to the directory containing PDF files.
- `output_directory` (string, required): Directory where the results file will be saved.
- `model` (string, optional): Qwen model identifier (default: `qwen-vl-max`).
- `dpi` (integer, optional): Scan resolution (default: `220`).

**Return Value:**
```json
{
  "status": "success",
  "job_id": "job_20251229_120000_a1b2c3d4",
  "summary": "Processed 40 files. Found 40 student records.",
  "output_file": "/absolute/path/to/output/job_20251229_120000_a1b2c3d4.jsonl",
  "errors": null
}
```

**Agent Strategy:**
- **Do not** attempt to read 40 PDFs directly.
- Call this tool first.
- Use the returned `output_file` to read the structured text.

### 2. Single File OCR
**Tool Name:** `process_pdf_document`

**Purpose:** 
Debug or single-file processing. Returns full text content in the tool response (use with caution for context limits).

**Parameters:**
- `pdf_path` (string, required): Path to the PDF.
- `unknown_label` (string, optional): Default name if detection fails.

### 3. Image Text Extraction
**Tool Name:** `extract_text_from_image`

**Purpose:** 
OCR for a single image file (JPG, PNG).

---

## Data Formats

### OCR Output (JSONL)
The `batch_process_documents` tool generates a JSON Lines file. Each line represents one student's work.

**Schema:**
```json
{
  "job_id": "job_20251229_...",
  "student_name": "Jane Doe",
  "text": "Full extracted text content of the essay...",
  "metadata": {
    "original_pdf": "/path/to/source.pdf",
    "start_page": 1,
    "end_page": 2,
    "page_count": 2
  }
}
```

### Scrubbed Output (JSONL)
*Note: Scrubbing logic is typically applied after OCR. Results follow the same schema but with PII redacted.*

**Schema:**
```json
{
  "job_id": "job_20251229_...",
  "student_name": "[STUDENT_NAME]",
  "text": "ID: [STUDENT_NAME] ... content ...",
  "metadata": { ... }
}
```

---

## Agent Integration Patterns

### Pattern 1: The Grading Loop
1.  **User Request:** "Grade the essays in `/data/input` using this rubric..."
2.  **Agent Action:** 
    - Call `batch_process_documents(directory_path="/data/input", output_directory="/data/jobs")`.
3.  **Server Response:** Returns `job_id` and `output_file` path.
4.  **Agent Action:**
    - Read `output_file` line-by-line.
    - For each line (student):
        - Extract `text`.
        - Apply Rubric (LLM inference).
        - Generate Feedback.
5.  **Final Output:** Compile grades into a CSV or report.

### Pattern 2: Context Retrieval
- **Memory**: The server is designed to work with "Context" (Subject Matter) retrieved via RAG and "Rubrics" provided by the user.
- **Scrubbing**: Ensure `text` is free of PII before sending to external model endpoints if privacy is a concern.

---

## Configuration & Environment

The agent should ensure the server has the following environment variables set:
- `QWEN_API_KEY`: Required for OCR.
- `QWEN_BASE_URL`: Optional (defaults to DashScope or OpenRouter based on key).
- `QWEN_API_MODEL`: Optional (default `qwen-vl-max`).

## Error Handling

- **Partial Failures**: The batch tool continues processing even if individual files fail. Check the `errors` list in the return value.
- **Empty Directories**: Returns a `warning` status.
- **Context Limits**: If `output_file` is huge, the agent should read it in chunks or use a tool to query it (e.g., `read_file` with `offset`).
