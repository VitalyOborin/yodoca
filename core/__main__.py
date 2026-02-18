"""Allow running the AI agent as a module: python -m core (invoked by Supervisor)."""

from core.runner import main

if __name__ == "__main__":
    main()
