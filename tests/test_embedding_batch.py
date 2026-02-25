"""Tests for embed_batch in EmbeddingExtension."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_embed_ext = Path(__file__).resolve().parent.parent / "sandbox" / "extensions" / "embedding"
sys.path.insert(0, str(_embed_ext))

from main import EmbeddingExtension


@pytest.fixture
def mock_embedder():
    """Mock EmbeddingCapability with embed_batch."""
    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(
        return_value=[[0.1] * 256, [0.2] * 256]
    )
    return embedder


@pytest.fixture
def ext_with_embedder(mock_embedder):
    """EmbeddingExtension with mocked embedder."""
    ext = EmbeddingExtension()
    ext._embedder = mock_embedder
    ext._default_model = "text-embedding-3-large"
    ext._default_dimensions = 256
    return ext


class TestEmbedBatch:
    """embed_batch() behavior."""

    @pytest.mark.asyncio
    async def test_embed_batch_returns_embeddings(
        self, ext_with_embedder: EmbeddingExtension, mock_embedder
    ) -> None:
        result = await ext_with_embedder.embed_batch(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [0.1] * 256
        assert result[1] == [0.2] * 256
        mock_embedder.embed_batch.assert_called_once()
        call_args = mock_embedder.embed_batch.call_args
        assert call_args.args[0] == ["hello", "world"]

    @pytest.mark.asyncio
    async def test_embed_batch_handles_empty_strings(
        self, ext_with_embedder: EmbeddingExtension
    ) -> None:
        ext_with_embedder._embedder.embed_batch = AsyncMock(
            return_value=[None, [0.1] * 256, None, [0.2] * 256]
        )
        result = await ext_with_embedder.embed_batch(["", "hello", "  ", "world"])
        assert len(result) == 4
        assert result[0] is None
        assert result[1] == [0.1] * 256
        assert result[2] is None
        assert result[3] == [0.2] * 256

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list(
        self, ext_with_embedder: EmbeddingExtension
    ) -> None:
        result = await ext_with_embedder.embed_batch([])
        assert result == []
        ext_with_embedder._embedder.embed_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_batch_no_embedder_returns_nones(self) -> None:
        ext = EmbeddingExtension()
        ext._embedder = None
        result = await ext.embed_batch(["hello", "world"])
        assert result == [None, None]
