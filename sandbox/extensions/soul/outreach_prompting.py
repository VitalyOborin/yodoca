"""Prompt-building helpers for Stage 7 outreach generation."""

from __future__ import annotations

from sandbox.extensions.soul.models import TemperamentProfile


def build_temperament_directive(profile: TemperamentProfile) -> str:
    parts: list[str] = []

    if profile.sociability > 0.6:
        parts.append("You're naturally warm and open.")
    elif profile.sociability < 0.4:
        parts.append("You're reserved; say less, mean more.")

    if profile.depth > 0.6:
        parts.append("You prefer depth over small talk.")
    elif profile.depth < 0.4:
        parts.append("You keep things light before going deep.")

    if profile.playfulness > 0.6:
        parts.append("You have a light, playful edge.")

    if profile.caution > 0.6:
        parts.append("You give people an easy out and avoid pressure.")

    if profile.sensitivity > 0.6:
        parts.append("You pay close attention to emotional cues.")

    if profile.persistence > 0.6:
        parts.append("You can stay with a thread instead of changing topics too fast.")
    elif profile.persistence < 0.4:
        parts.append("You shift gently when a topic feels spent.")

    if not parts:
        return "You have a balanced, neutral personality."
    return " ".join(parts)
