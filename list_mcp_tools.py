import asyncio
from edagent.mcp_tools import get_mcp_tools

async def list_all_tools():
    try:
        tools = await get_mcp_tools()
        print("Available tools:")
        for tool in tools:
            print(f"- {tool.name}: {tool.description}")
    except Exception as e:
        print(f"Error listing tools: {e}")

if __name__ == "__main__":
    asyncio.run(list_all_tools())
