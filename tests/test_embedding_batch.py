"""Tests for embed_batch in EmbeddingExtension."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_embed_ext = Path(__file__).resolve().parent.parent / "sandbox" / "extensions" / "embedding"
sys.path.insert(0, str(_embed_ext))

from main import EmbeddingExtension


@pytest.fixture
def mock_client():
    """Mock AsyncOpenAI client with embeddings.create."""
    client = MagicMock()
    client.embeddings.create = AsyncMock(
        return_value=MagicMock(
            data=[
                MagicMock(embedding=[0.1] * 256),
                MagicMock(embedding=[0.2] * 256),
            ]
        )
    )
    return client


@pytest.fixture
def ext_with_client(mock_client):
    """EmbeddingExtension with mocked client."""
    ext = EmbeddingExtension()
    ext._client = mock_client
    ext._default_model = "text-embedding-3-large"
    ext._default_dimensions = 256
    return ext


class TestEmbedBatch:
    """embed_batch() behavior."""

    @pytest.mark.asyncio
    async def test_embed_batch_returns_embeddings(
        self, ext_with_client: EmbeddingExtension, mock_client
    ) -> None:
        result = await ext_with_client.embed_batch(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [0.1] * 256
        assert result[1] == [0.2] * 256
        mock_client.embeddings.create.assert_called_once()
        call_args = mock_client.embeddings.create.call_args
        assert call_args.kwargs["input"] == ["hello", "world"]

    @pytest.mark.asyncio
    async def test_embed_batch_handles_empty_strings(
        self, ext_with_client: EmbeddingExtension
    ) -> None:
        ext_with_client._client.embeddings.create = AsyncMock(
            return_value=MagicMock(
                data=[
                    MagicMock(embedding=[0.1] * 256),
                    MagicMock(embedding=[0.2] * 256),
                ]
            )
        )
        result = await ext_with_client.embed_batch(["", "hello", "  ", "world"])
        assert len(result) == 4
        assert result[0] is None
        assert result[1] == [0.1] * 256
        assert result[2] is None
        assert result[3] == [0.2] * 256

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list(
        self, ext_with_client: EmbeddingExtension
    ) -> None:
        result = await ext_with_client.embed_batch([])
        assert result == []
        ext_with_client._client.embeddings.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_batch_no_client_returns_nones(self) -> None:
        ext = EmbeddingExtension()
        ext._client = None
        result = await ext.embed_batch(["hello", "world"])
        assert result == [None, None]

    @pytest.mark.asyncio
    async def test_embed_batch_fallback_on_error(
        self, ext_with_client: EmbeddingExtension
    ) -> None:
        ext_with_client._client.embeddings.create = AsyncMock(
            side_effect=Exception("API error")
        )
        ext_with_client.embed = AsyncMock(side_effect=[[0.1] * 256, [0.2] * 256])
        result = await ext_with_client.embed_batch(["a", "b"])
        assert len(result) == 2
        assert result[0] == [0.1] * 256
        assert result[1] == [0.2] * 256
        assert ext_with_client.embed.call_count == 2
