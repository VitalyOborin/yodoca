# ADR 001: Supervisor and AI Agent as Separate Processes

## Status

Accepted. Implemented

## Context

The AI agent (Orchestrator) must not be started directly. It must run under a Supervisor so that:

- The Supervisor is the single parent process and the AI agent runs in a child process.
- The Supervisor can stop the AI agent process on command.
- When the Supervisor process stops, the child AI agent process must also terminate.

## Decision

- **Supervisor** runs in its own process. It is the only allowed entry point for running the application (`python -m supervisor`).
- **AI agent (core)** runs in a separate process, spawned and controlled by the Supervisor (e.g. `subprocess.Popen(..., args=["python", "-m", "core"])`).
- Supervisor keeps a reference to the child process and:
  - On a defined "stop" command (e.g. user input or signal), terminates the child (e.g. `process.terminate()` / `SIGTERM`).
  - On Supervisor exit (shutdown, signal, or error), ensures the child is terminated (e.g. terminate on exit, or use a process group so the child is killed when the parent dies).
- The child process does not daemonise; it is a normal subprocess so that it is tied to the Supervisor’s lifecycle.

## Consequences

- Users always start the app via Supervisor; the AI agent is never run via `python -m core` by end users in production.
- Supervisor can stop the agent at any time without exiting itself (if we add a "stop" command) or can exit and take down the child.
- Clean shutdown: Supervisor’s exit path must always terminate the child process.
