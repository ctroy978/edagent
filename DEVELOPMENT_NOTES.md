# EdAgent Development Notes

**Last Updated:** December 29, 2024

## Project Overview
EdAgent is a multi-agent LangGraph system for grading student work (essays, tests) using OCR and AI. Built with LangGraph, Chainlit UI, FastMCP tools, and xAI's Grok-4.

---

## Architecture

### **Router → Specialist Pattern**
```
User Input → Router Node → [Essay Grading | Test Grading | General] → Response
```

- **Router Node**: Analyzes user intent and routes to appropriate specialist
- **Essay Grading Node**: Handles essay evaluation with OCR tools
- **Test Grading Node**: Handles tests/quizzes (future implementation)
- **General Node**: Handles non-grading questions

### **Key Components**
- `edagent/app.py` - Chainlit UI and message handling
- `edagent/graph.py` - LangGraph workflow with memory
- `edagent/nodes.py` - Router and specialist nodes
- `edagent/mcp_tools.py` - MCP server connection and tool loading
- `edagent/state.py` - State schema
- `/home/tcoop/Work/edmcp/` - Separate MCP server with OCR tools

---

## Design Principles

### 1. **Natural Language Understanding - NOT Strict Keyword Matching**
❌ **WRONG**: Looking for specific phrases like "I want to grade essays"
✅ **RIGHT**: LLM understands intent from natural variations
   - "My students finished their essays"
   - "I need to evaluate some papers"
   - "Can you help grade writing assignments"

**Key Point**: The agent uses Grok's natural language understanding. Don't write code that expects specific user inputs. The LLM handles variations naturally.

### 2. **Conversational, Not Overwhelming**
- Ask **ONE question at a time**
- Don't dump all requirements upfront
- Guide users step-by-step through workflows
- Be brief and friendly

**Example of GOOD flow:**
```
Agent: "Are these handwritten or typed essays?"
User: "handwritten"
Agent: "Have they been scanned to PDF already?"
User: "no"
Agent: "You'll need to scan them first. Once done, where will the PDFs be?"
```

**Example of BAD flow (what we fixed):**
```
Agent: "DOCUMENT PREPARATION REQUIREMENTS: 1. Scan to PDF 2. Name format... 
[10 paragraphs of requirements]... Do you understand?"
```

### 3. **No Internal Thinking Shown to User**
- Use **non-reasoning models** for agents (grok-4, NOT grok-4-fast-reasoning)
- Reasoning models show internal monologue - bad UX for agents
- System prompts must say: "NEVER show your internal thinking or reasoning to the user"
- Set `hide_cot = true` in Chainlit config

### 4. **LangGraph Memory for Context Retention**
- Use LangGraph's built-in `MemorySaver` with thread IDs
- DON'T manually manage conversation history in sessions
- Each user session gets unique `thread_id` for memory persistence
- Enables multi-turn conversations without losing context

**Implementation:**
```python
memory = MemorySaver()
graph = workflow.compile(checkpointer=memory)

# In app.py
thread_id = cl.user_session.get("thread_id") or str(uuid.uuid4())
config = {"configurable": {"thread_id": thread_id}}
graph.astream(initial_state, config)
```

### 5. **Specialized Nodes for Different Tasks**
- Don't create one "grading" node that does everything
- Create **specialized nodes** for different grading types:
  - Essay Grading: Evaluates writing quality, structure, arguments
  - Test Grading: Scores objective answers, answer keys
  - Each has different evaluation criteria and workflows

**Why?** Different assignment types need different approaches. The router determines which specialist to use based on user intent.

### 6. **File Attachments Support**
- Users can attach files via Chainlit's 📎 button
- Agent should mention this option when asking for files
- Extract file paths from `message.elements` and append to message content
- Format: `[User attached files: /path/to/file.pdf]`

**Don't assume** users know file paths - offer attachment as easier option.

---

## Technical Configuration

### **Model Selection**
- **Current**: `XAI_MODEL=grok-4` (non-reasoning)
- **Don't use**: `grok-4-fast-reasoning` (shows internal thinking, bad for agents)
- Standard models better for tool calling and conversational flow

### **MCP Server Connection**
- MCP server runs in separate venv at `/home/tcoop/Work/edmcp/.venv`
- Must use MCP server's Python interpreter, not edagent's
- Path resolution: `server_dir/.venv/bin/python`

**Tools Available:**
1. `batch_process_documents(directory_path, output_directory)` - Batch PDF processing
2. `process_pdf_document(pdf_path)` - Single PDF
3. `extract_text_from_image(image_path)` - Image OCR
4. `read_file(file_path)` - Read files (e.g., JSONL output)

### **Document Requirements (MCP System)**
For OCR to work correctly:
- Student name MUST be on **first page only**: "Name: John Doe"
- Format: `Name: <name>` or `Name - <name>` or `ID: <name>`
- Subsequent pages should NOT have names (auto-grouped)
- Optional: "Continue: John Doe" on later pages for explicit linking
- Missing names → labeled "Unknown Student 01", etc.

**Regex pattern**: `(?im)^\s*(?:name|id)\s*[:\-]\s*([\p{L}][\p{L}'-]*(?:\s+[\p{L}][\p{L}'-]*)?)`

### **Chainlit Configuration**
- `hide_cot = true` - Hide chain of thought
- `spontaneous_file_upload.enabled = true` - Enable file uploads
- Config location: `.chainlit/config.toml`

---

## Current State & Next Steps

### **Working Features** ✅
- Router correctly identifies essay grading intent
- Essay grading node guides conversationally through workflow
- LangGraph memory maintains context across messages
- File attachment detection and extraction
- MCP tools connection working
- Non-reasoning model (grok-4) for clean responses

### **Needs Implementation** 🚧
1. **Actual grading execution**: Node asks questions but doesn't call tools yet
2. **Rubric processing**: Read and parse rubric files (attached or path)
3. **JSONL parsing**: After batch_process_documents, parse results
4. **Feedback generation**: Apply rubric to each essay and generate grades
5. **Test grading node**: Build out test grading specialist
6. **Error handling**: Better error messages for common issues

### **Future Enhancements** 💡
- Multiple choice test grading with answer keys
- Bubble sheet processing
- Batch feedback export (CSV/Excel)
- Custom rubric templates
- Progress indicators for batch processing

---

## Common Issues & Solutions

### **Issue**: Agent loses context after first response
**Solution**: Use LangGraph memory with thread IDs (implemented)

### **Issue**: Agent shows internal thinking/reasoning
**Solution**: Use non-reasoning model (grok-4, not grok-4-fast-reasoning)

### **Issue**: MCP tools fail with "fastmcp not found"
**Solution**: Use MCP server's venv Python, not edagent's Python

### **Issue**: Agent dumps all requirements upfront
**Solution**: Update system prompt to guide conversationally, one question at a time

### **Issue**: Agent expects exact user phrases
**Solution**: Trust the LLM's natural language understanding - don't write strict keyword matching

---

## Key Files to Know

### Configuration
- `.env` - API keys and model selection (XAI_MODEL=grok-4)
- `.chainlit/config.toml` - UI settings (hide_cot=true)

### Core Code
- `edagent/nodes.py` - **Most important** - Contains all agent logic and prompts
- `edagent/graph.py` - LangGraph workflow definition with memory
- `edagent/app.py` - Chainlit UI, message handling, file attachments
- `edagent/mcp_tools.py` - MCP server connection and tool loading

### External
- `/home/tcoop/Work/edmcp/server.py` - MCP server with OCR tools (separate project)

---

## Testing Checklist

When testing the agent, verify:
- [ ] Natural language variations work (don't test with exact phrases)
- [ ] Context maintained across multiple messages
- [ ] No internal thinking shown in responses
- [ ] File attachment option mentioned when relevant
- [ ] One question at a time (not overwhelming)
- [ ] Router correctly identifies essay vs test grading
- [ ] MCP tools load without errors

---

## Onboarding Quick Start

1. **Read this document** (you're doing it!)
2. **Check `.env`** - Ensure `XAI_MODEL=grok-4` (non-reasoning)
3. **Review** `edagent/nodes.py` - All agent behavior defined here
4. **Test conversation flow** - Focus on natural language, not keywords
5. **Next task**: Implement actual tool calling in essay grading node (step 6+ in workflow)

---

## Development Philosophy

**Trust the LLM.** We're using Grok-4, a powerful language model. Don't overthink prompts or try to control every variation. Give clear guidance on behavior, then let the model handle natural conversation.

**Conversational > Procedural.** This is an AI assistant, not a form to fill out. Guide users naturally through workflows.

**Specialized > Generic.** Better to have focused specialist nodes than one node trying to do everything.

**Memory matters.** Multi-turn workflows NEED context retention. Use LangGraph's built-in memory.

---

## Contact & Resources

- MCP Tools Documentation: Check `/home/tcoop/Work/edmcp/README.md`
- LangGraph Memory: https://langchain-ai.github.io/langgraph/how-tos/persistence/
- Chainlit Docs: https://docs.chainlit.io/

---

**Remember**: The goal is to create a helpful, conversational assistant that understands teachers naturally - not a rigid command-line interface disguised as chat.
