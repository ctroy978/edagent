# EdAgent - Educational AI Assistant

A multi-agent system built with **LangGraph**, **Chainlit**, and **FastMCP** to assist teachers with grading and curriculum design.

## Overview

EdAgent is an intelligent teaching assistant that routes requests to specialized agents:

- **Grading Specialist** - Processes and grades student submissions (PDFs), provides feedback
- **Curriculum Specialist** - Designs lesson plans, creates learning objectives, structures courses
- **General Assistant** - Handles general educational questions and guidance

The system uses a "Router-Expert" pattern where a Concierge agent analyzes user intent and routes to the appropriate specialist.

## Architecture

```
User Input → Router (Concierge) → [Grading Expert | Curriculum Expert | General Chat]
```

- **LangGraph**: State management and routing logic
- **Chainlit**: Chat UI with quick-start buttons
- **FastMCP**: Local tool server (via stdio) for document processing
- **LangChain**: LLM orchestration with xAI (Grok), OpenAI, or Anthropic

## Prerequisites

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) package manager
- xAI API key (recommended) or OpenAI/Anthropic API key
- EDMCP FastMCP server (located at `/home/tcoop/Work/edmcp`)

## Getting Your xAI API Key

1. Visit [x.ai](https://x.ai) and sign up/login
2. Navigate to the API section
3. Generate a new API key
4. Copy the key (starts with `xai-`)

## Installation

1. **Clone and navigate to the project:**
   ```bash
   cd /home/tcoop/Work/edagent
   ```

2. **Install dependencies with uv:**
   ```bash
   uv sync
   ```

3. **Create a `.env` file:**
   ```bash
   cp .env.example .env
   ```

4. **Configure your `.env` file:**
   ```env
   # xAI (Recommended) - Fast and powerful
   XAI_API_KEY=xai-your-key-here
   XAI_MODEL=grok-2-1212
   
   # Alternative providers (if not using xAI)
   # OPENAI_API_KEY=sk-...
   # OPENAI_MODEL=gpt-4-turbo-preview
   # ANTHROPIC_API_KEY=sk-ant-...
   # ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
   
   # Path to your FastMCP server
   MCP_SERVER_PATH=/home/tcoop/Work/edmcp/server.py
   ```

### Available xAI Models

- `grok-2-1212` (Recommended) - Latest and most capable
- `grok-2-latest` - Always points to the newest Grok 2 version
- `grok-2` - Stable Grok 2 release
- `grok-beta` - Beta features
- `grok-vision-beta` - With vision capabilities

## Usage

### Running the Application

**Option 1: Using uv run (recommended)**
```bash
uv run python main.py
```

**Option 2: Using chainlit directly**
```bash
uv run chainlit run edagent/app.py -w
```

The application will launch at `http://localhost:8000`

### Using the Interface

1. **Welcome Screen**: When you first load the app, you'll see a welcome message and quick-start buttons

2. **Quick-Start Buttons**:
   - 📝 Grade Student Essays
   - 📚 Design a Lesson Plan
   - 🔍 Process PDF Documents
   - 💡 Ask a Question

3. **Natural Language**: Simply type your request in the chat! The router will automatically detect your intent and route to the appropriate specialist.

### Example Workflows

**Grading Essays:**
```
User: "I need to grade 40 student essays in the /data/essays folder"
Router: Routes to Grading Specialist
Grading Agent: 
  1. Asks for rubric (if not provided)
  2. Calls batch_process_documents tool
  3. Reads JSONL output
  4. Applies rubric to each essay
  5. Returns detailed feedback
```

**Creating Curriculum:**
```
User: "Help me design a 3-week unit on photosynthesis for 8th grade"
Router: Routes to Curriculum Specialist
Curriculum Agent:
  1. Clarifies learning objectives
  2. Suggests daily lesson breakdown
  3. Recommends activities and assessments
  4. Provides implementation tips
```

## Project Structure

```
edagent/
├── edagent/
│   ├── __init__.py          # Package initialization
│   ├── app.py               # Chainlit UI application
│   ├── graph.py             # LangGraph construction
│   ├── nodes.py             # Router and expert nodes
│   ├── state.py             # State schema (TypedDict)
│   └── mcp_tools.py         # MCP client connection factory
├── main.py                  # Entry point
├── .env.example             # Environment template
├── .chainlit                # Chainlit configuration
├── pyproject.toml           # Dependencies (uv)
└── README.md                # This file
```

## Key Components

### 1. MCP Tool Integration (`mcp_tools.py`)

Connects to the local FastMCP server via stdio and converts tools to LangChain-compatible format:

```python
async with get_mcp_session() as session:
    tools = await session.list_tools()
    # Convert to LangChain StructuredTools
```

### 2. Router/Concierge Node (`nodes.py`)

Analyzes user intent using structured output:

```python
class RouterDecision(BaseModel):
    reasoning: str
    next_step: Literal["grading", "curriculum", "general"]
```

### 3. Expert Nodes

Each specialist has:
- Custom system prompts
- Access to relevant tools (grading has MCP tools)
- Conversational memory via state

### 4. LangGraph (`graph.py`)

Defines the routing workflow:

```python
workflow.add_conditional_edges(
    "router",
    route_decision,
    {
        "grading": "grading",
        "curriculum": "curriculum", 
        "general": "general"
    }
)
```

## Configuration

### Chainlit Settings

Edit `.chainlit` to customize:
- UI theme and colors
- Session timeout
- Feature flags (prompt playground, file uploads)

### Environment Variables

- `XAI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`: LLM provider (xAI recommended)
- `MCP_SERVER_PATH`: Absolute path to FastMCP server script
- `XAI_MODEL` / `OPENAI_MODEL` / `ANTHROPIC_MODEL`: Optional model overrides

## Troubleshooting

**Issue: "No API key found"**
- Solution: Make sure your `.env` file contains either XAI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY

**Issue: "MCP_SERVER_PATH environment variable not set"**
- Solution: Make sure your `.env` file exists and contains the correct path

**Issue: "Import errors" during development**
- Solution: The IDE may not recognize the virtual environment. Errors will resolve when running with `uv run`

**Issue: MCP server connection fails**
- Solution: Verify the server script exists and is executable:
  ```bash
  ls -la /home/tcoop/Work/edmcp/server.py
  chmod +x /home/tcoop/Work/edmcp/server.py
  ```

**Issue: Tools not loading**
- Solution: Check that the MCP server implements the required tools (`batch_process_documents`, etc.)

## Development

### Adding New Specialists

1. Create a new node function in `nodes.py`:
   ```python
   async def my_specialist_node(state: AgentState) -> AgentState:
       # Implementation
   ```

2. Add routing logic in the Router's system prompt

3. Register the node in `graph.py`:
   ```python
   workflow.add_node("my_specialist", my_specialist_node)
   workflow.add_edge("my_specialist", END)
   ```

4. Update conditional edges to include new route

### Testing

```bash
# Run with debug logging
CHAINLIT_DEBUG=true uv run chainlit run edagent/app.py

# Test MCP connection separately
uv run python -c "from edagent.mcp_tools import get_mcp_tools; import asyncio; asyncio.run(get_mcp_tools())"
```

## Contributing

This is an internal educational tool. For questions or issues, contact the development team.

## License

Internal use only.
