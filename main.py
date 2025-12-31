"""Main entry point for EdAgent application."""

import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    """Run the EdAgent Chainlit application."""
    print("Starting EdAgent...")
    print("Make sure you have:")
    print("1. Created a .env file with your API keys")
    print("2. Set MCP_SERVER_PATH to your FastMCP server")
    print("\nLaunching Chainlit interface...")

    # Run chainlit with PYTHONPATH set to include project root
    import subprocess

    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
    subprocess.run(["chainlit", "run", "edagent/app.py", "-w"], env=env)


if __name__ == "__main__":
    main()
