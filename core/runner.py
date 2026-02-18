"""Entry point for the AI agent process: runs the Orchestrator and prints to CLI."""

from dotenv import load_dotenv

from core.agents.orchestrator import main

# Load .env so OPENAI_API_KEY is available in the child process.
load_dotenv()

__all__ = ["main"]
