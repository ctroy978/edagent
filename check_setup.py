"""Quick setup verification script for EdAgent."""

import os
import sys
from pathlib import Path


def check_env_file():
    """Check if .env file exists and has required keys."""
    env_path = Path(".env")
    if not env_path.exists():
        print("❌ .env file not found")
        print("   Run: cp .env.example .env")
        return False

    print("✓ .env file exists")

    # Check for API keys
    with open(env_path) as f:
        content = f.read()
        has_xai = "XAI_API_KEY" in content and not content.split("XAI_API_KEY")[
            1
        ].split("\n")[0].strip().endswith("...")
        has_openai = "OPENAI_API_KEY" in content and not content.split(
            "OPENAI_API_KEY"
        )[1].split("\n")[0].strip().endswith("...")
        has_anthropic = "ANTHROPIC_API_KEY" in content and not content.split(
            "ANTHROPIC_API_KEY"
        )[1].split("\n")[0].strip().endswith("...")
        has_mcp_path = "MCP_SERVER_PATH" in content and not content.split(
            "MCP_SERVER_PATH"
        )[1].split("\n")[0].strip().endswith(".py...")

        if has_xai:
            print("✓ xAI API key configured (Recommended)")
        elif has_openai or has_anthropic:
            print("✓ API key configured")
        else:
            print(
                "❌ No API key found (XAI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY)"
            )
            return False

        if has_mcp_path:
            print("✓ MCP_SERVER_PATH configured")
        else:
            print("⚠️  MCP_SERVER_PATH not configured")

    return True


def check_mcp_server():
    """Check if MCP server path exists."""
    from dotenv import load_dotenv

    load_dotenv()

    mcp_path = os.getenv("MCP_SERVER_PATH")
    if not mcp_path:
        print("⚠️  MCP_SERVER_PATH not set in .env")
        return False

    if not Path(mcp_path).exists():
        print(f"❌ MCP server not found at: {mcp_path}")
        return False

    print(f"✓ MCP server found at: {mcp_path}")
    return True


def check_dependencies():
    """Check if key dependencies are importable."""
    deps = {
        "langgraph": "LangGraph",
        "langchain": "LangChain",
        "langchain_xai": "LangChain xAI",
        "chainlit": "Chainlit",
        "mcp": "MCP SDK",
        "dotenv": "python-dotenv",
    }

    all_ok = True
    for module, name in deps.items():
        try:
            __import__(module)
            print(f"✓ {name} installed")
        except ImportError:
            print(f"❌ {name} not installed")
            all_ok = False

    return all_ok


def main():
    """Run all checks."""
    print("=" * 60)
    print("EdAgent Setup Verification")
    print("=" * 60)
    print()

    print("Checking environment configuration...")
    env_ok = check_env_file()
    print()

    print("Checking MCP server...")
    mcp_ok = check_mcp_server()
    print()

    print("Checking dependencies...")
    deps_ok = check_dependencies()
    print()

    print("=" * 60)
    if env_ok and mcp_ok and deps_ok:
        print("✓ Setup complete! Ready to run:")
        print("  uv run python main.py")
    else:
        print("⚠️  Some issues found. Please fix them before running.")
        print("\nQuick fixes:")
        if not env_ok:
            print("  1. Copy .env.example to .env")
            print("  2. Add your xAI API key (XAI_API_KEY=xai-...)")
            print("     Get your key at: https://x.ai")
        if not mcp_ok:
            print("  3. Set MCP_SERVER_PATH to the correct path")
        if not deps_ok:
            print("  4. Run: uv sync")
    print("=" * 60)


if __name__ == "__main__":
    main()
