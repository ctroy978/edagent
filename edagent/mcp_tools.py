"""MCP Tool Factory - Connects to local FastMCP server via stdio."""

import os
import json
from typing import List, Any
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model


@asynccontextmanager
async def get_mcp_session():
    """Create and manage MCP client session via stdio.

    Yields:
        ClientSession: Active MCP client session
    """
    server_path = os.getenv("MCP_SERVER_PATH")
    if not server_path:
        raise ValueError("MCP_SERVER_PATH environment variable not set")

    if not os.path.exists(server_path):
        raise FileNotFoundError(f"MCP server script not found: {server_path}")

    # Determine Python interpreter - use the venv from the MCP server directory
    # The MCP server should be in a directory with its own .venv
    server_dir = os.path.dirname(os.path.abspath(server_path))
    venv_python = os.path.join(server_dir, ".venv", "bin", "python")

    if os.path.exists(venv_python):
        python_path = venv_python
    else:
        # Fallback to system python if venv not found
        python_path = "python"

    server_params = StdioServerParameters(
        command=python_path,
        args=[server_path],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _json_schema_to_pydantic(name: str, schema: dict) -> type[BaseModel]:
    """Convert JSON Schema to Pydantic model for tool input validation.

    Args:
        name: Name for the Pydantic model
        schema: JSON Schema definition

    Returns:
        Pydantic BaseModel class
    """
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    field_definitions = {}
    for prop_name, prop_schema in properties.items():
        field_type = str  # Default to string
        field_description = prop_schema.get("description", "")
        field_default = ... if prop_name in required else None

        # Map JSON schema types to Python types
        json_type = prop_schema.get("type", "string")
        if json_type == "integer":
            field_type = int
        elif json_type == "number":
            field_type = float
        elif json_type == "boolean":
            field_type = bool
        elif json_type == "array":
            field_type = list
        elif json_type == "object":
            field_type = dict

        field_definitions[prop_name] = (
            field_type,
            Field(default=field_default, description=field_description),
        )

    return create_model(name, **field_definitions)


async def get_mcp_tools() -> List[StructuredTool]:
    """Fetch tools from MCP server and convert to LangChain tools.

    Returns:
        List of LangChain StructuredTool instances
    """
    tools = []

    async with get_mcp_session() as session:
        # List available tools from MCP server
        result = await session.list_tools()

        for tool_info in result.tools:
            tool_name = tool_info.name
            tool_description = tool_info.description or f"MCP tool: {tool_name}"

            # Convert input schema to Pydantic model
            input_schema = tool_info.inputSchema

            # SPECIAL HANDLING: batch_process_documents
            # Make dpi optional in the schema so the agent doesn't worry about it
            # We will inject the default value in the execution wrapper
            if tool_name == "batch_process_documents":
                if "required" in input_schema and "dpi" in input_schema["required"]:
                    input_schema["required"].remove("dpi")

            args_schema = _json_schema_to_pydantic(f"{tool_name}_input", input_schema)

            # Create async wrapper function for tool execution
            async def make_tool_func(name: str):
                async def tool_func(**kwargs) -> str:
                    """Execute MCP tool and return result."""
                    
                    # Inject default DPI for batch processing if not provided or explicitly None
                    if name == "batch_process_documents":
                        if "dpi" not in kwargs or kwargs["dpi"] is None:
                            kwargs["dpi"] = 300

                    async with get_mcp_session() as sess:
                        result = await sess.call_tool(name, arguments=kwargs)

                        # Extract text content from result
                        if result.content:
                            return "\n".join(
                                item.text
                                for item in result.content
                                if hasattr(item, "text")
                            )
                        return "Tool executed successfully (no output)"

                return tool_func

            # Create LangChain StructuredTool
            tool = StructuredTool(
                name=tool_name,
                description=tool_description,
                args_schema=args_schema,
                coroutine=await make_tool_func(tool_name),
            )
            tools.append(tool)

    return tools


async def get_grading_tools() -> List[StructuredTool]:
    """Get tools relevant to grading tasks.

    Returns:
        Filtered list of grading-related tools including the full pipeline
    """
    all_tools = await get_mcp_tools()
    # Include ALL tools needed for complete grading pipeline
    grading_keywords = [
        "batch_process",
        "extract_text",
        "get_job_statistics",
        "scrub_processed_job",
        "normalize_processed_job",
        "evaluate_job",
        "generate_gradebook",
        "generate_student_feedback",
        "add_to_knowledge_base",
        "query_knowledge_base",
        "search_past_jobs",
        "export_job_archive",
    ]
    return [
        tool
        for tool in all_tools
        if any(keyword in tool.name.lower() for keyword in grading_keywords)
    ]
