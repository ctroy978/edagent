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

            # SPECIAL HANDLING: convert_pdf_to_text
            # Make use_ocr optional in the schema
            # We will inject the default value in the execution wrapper
            if tool_name == "convert_pdf_to_text":
                if "required" in input_schema and "use_ocr" in input_schema["required"]:
                    input_schema["required"].remove("use_ocr")

            args_schema = _json_schema_to_pydantic(f"{tool_name}_input", input_schema)

            # Create async wrapper function for tool execution
            async def make_tool_func(name: str):
                async def tool_func(**kwargs) -> str:
                    """Execute MCP tool and return result."""

                    # Inject default DPI for batch processing if not provided or explicitly None
                    if name == "batch_process_documents":
                        if "dpi" not in kwargs or kwargs["dpi"] is None:
                            kwargs["dpi"] = 300

                    # Inject default use_ocr for PDF conversion if not provided or explicitly None
                    if name == "convert_pdf_to_text":
                        if "use_ocr" not in kwargs or kwargs["use_ocr"] is None:
                            kwargs["use_ocr"] = False  # Default to fast text extraction

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
    # Include ALL tools needed for complete grading pipeline (excluding email tools)
    grading_keywords = [
        "create_job",  # For creating jobs with materials
        "batch_process",
        "extract_text",
        "read_text",  # For reading .txt files
        "get_job_statistics",
        "scrub_processed_job",
        "normalize_processed_job",
        "evaluate_job",
        "generate_gradebook",
        "generate_student_feedback",
        "download_reports",  # For downloading reports from DB to local temp
        "add_to_knowledge_base",
        "query_knowledge_base",
        "search_past_jobs",
        "export_job_archive",
        "convert_pdf_to_text",
        "convert_image_to_pdf",
        "batch_convert",
        "merge_images",
    ]
    return [
        tool
        for tool in all_tools
        if any(keyword in tool.name.lower() for keyword in grading_keywords)
    ]


async def get_email_tools() -> List[StructuredTool]:
    """Get tools relevant to email distribution.

    Returns:
        Filtered list of email-related tools
    """
    all_tools = await get_mcp_tools()
    # Email distribution tools only
    email_keywords = [
        "identify_email_problems",
        "verify_student_name_correction",
        "apply_student_name_correction",
        "skip_student_email",
        "send_student_feedback_emails",
        "get_email_log",
    ]
    return [
        tool
        for tool in all_tools
        if any(keyword in tool.name.lower() for keyword in email_keywords)
    ]


async def get_phase_tools(phase: str) -> List[StructuredTool]:
    """Get tools for a specific workflow phase.

    This function enforces phase separation by only returning tools
    allowed for each phase of the essay grading workflow.

    Args:
        phase: One of 'gather', 'prepare', 'validate', 'scrub', 'inspect', 'evaluate', 'report'

    Returns:
        Filtered list of tools for that phase only

    Raises:
        ValueError: If invalid phase name provided
    """
    # Define phase-to-tool mappings
    phase_tool_map = {
        "gather": [
            "create_job_with_materials",  # Create job and store materials in DB
            "add_to_knowledge_base",      # Add reading materials to RAG
            "convert_pdf_to_text",        # Read PDF rubrics/questions
            "read_text_file",             # Read .txt rubrics/questions
        ],
        "prepare": [
            "batch_process_documents",    # OCR processing of essays
            # Note: prepare_files_for_grading is added separately (not from MCP)
        ],
        "validate": [
            "get_job_statistics",         # Retrieve student manifest
            "validate_student_names",     # Validate names against roster
            "correct_detected_name",      # Fix name mismatches
        ],
        "scrub": [
            "get_job_statistics",         # Retrieve student manifest (for confirmation)
            "scrub_processed_job",        # Remove PII from essays
        ],
        "inspect": [
            "get_job_statistics",         # Retrieve student manifest
            "validate_student_names",     # Validate names against roster
            "correct_detected_name",      # Fix name mismatches
            "scrub_processed_job",        # Remove PII from essays
        ],
        "evaluate": [
            "query_knowledge_base",       # Retrieve context from RAG
            "evaluate_job",               # Grade essays with rubric
        ],
        "report": [
            "generate_gradebook",         # Create CSV gradebook
            "generate_student_feedback",  # Create individual PDF reports
            "download_reports_locally",   # Download reports from DB to temp files
        ],
    }

    # Validate phase
    if phase not in phase_tool_map:
        raise ValueError(
            f"Invalid phase '{phase}'. Must be one of: {', '.join(phase_tool_map.keys())}"
        )

    # Get all tools from MCP server
    all_tools = await get_mcp_tools()

    # Filter to only tools allowed for this phase
    allowed_keywords = phase_tool_map[phase]
    return [
        tool
        for tool in all_tools
        if any(keyword in tool.name.lower() for keyword in allowed_keywords)
    ]
