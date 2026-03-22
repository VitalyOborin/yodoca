"""Token and cost accounting for tracing spans."""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken


@dataclass(frozen=True)
class Pricing:
    input_per_million: float
    output_per_million: float


class TokenCounter:
    """Token estimation + configurable pricing calculations."""

    APPROX_BUFFER = 1.1

    def __init__(self, pricing: dict[str, dict[str, float]] | None = None) -> None:
        self._pricing: dict[str, Pricing] = {}
        self.update_pricing(pricing or {})

    def update_pricing(self, pricing: dict[str, dict[str, float]]) -> None:
        parsed: dict[str, Pricing] = {}
        for model, model_pricing in pricing.items():
            parsed[model] = Pricing(
                input_per_million=float(model_pricing.get("input", 0.0)),
                output_per_million=float(model_pricing.get("output", 0.0)),
            )
        self._pricing = parsed

    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Count tokens using tiktoken encoder for model or cl100k_base fallback."""
        if not text:
            return 0
        try:
            if model:
                encoding = tiktoken.encoding_for_model(model)
            else:
                encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))

    def approximate_tokens(self, text: str) -> int:
        """Fast approximation when exact tokenizer is unavailable or unnecessary."""
        if not text:
            return 0
        return max(1, int((len(text) / 4.0) * self.APPROX_BUFFER))

    def calculate_cost(
        self,
        tokens_in: int,
        tokens_out: int,
        model: str | None,
    ) -> float:
        """Calculate USD cost with per-million pricing table."""
        if not model:
            return 0.0
        pricing = self._pricing.get(model)
        if not pricing:
            return 0.0
        in_cost = (tokens_in / 1_000_000) * pricing.input_per_million
        out_cost = (tokens_out / 1_000_000) * pricing.output_per_million
        return round(in_cost + out_cost, 8)
