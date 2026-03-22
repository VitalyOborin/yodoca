# ADR 031: Remove Matryoshka Embedding Reduction

## Status

Accepted. Implemented

## Context

The application currently assumes a fixed embedding size of 256 dimensions in the memory subsystem. This design came from OpenAI `text-embedding-3-large`, which supports native dimensionality reduction via the `dimensions` request parameter and is trained to preserve most relevant information in the leading portion of the vector.

That assumption does not hold for other embedding models and providers:

- Some OpenAI-compatible providers ignore the `dimensions` parameter and return the model's native vector size.
- Some local models, such as Jina embeddings served through LM Studio, return higher-dimensional vectors and are not trained for Matryoshka-style truncation.
- Truncating such vectors to 256 dimensions discards semantic information and materially degrades retrieval quality.

The current implementation also hardcodes `float[256]` in sqlite-vec tables and performs defensive truncation in memory storage when providers return larger vectors. As a result, the storage schema dictates the vector size instead of the embedding model.

The architecture needs to change so that memory stores embeddings in the model's native dimension and never applies Matryoshka reduction implicitly.

## Decision

### 1. Native embedding dimension only

The system will no longer request reduced embedding dimensions from providers and will no longer truncate embeddings in application code.

- `EmbeddingCapability` returns the model's native vector shape.
- OpenAI-compatible providers stop sending the `dimensions` parameter.
- Memory stores and queries vectors exactly as returned by the provider.

### 2. Memory vector schema is dimension-aware

The sqlite-vec schema for memory is no longer fixed in `schema.sql`.

- `vec_nodes` and `vec_entities` are created dynamically by `MemoryStorage`.
- The active embedding dimension is persisted in `maintenance_metadata` under the `embedding_dim` key.
- On startup, memory probes the embedding model once to determine the native dimension when an embedder is available.

### 3. Safe startup behavior

The startup flow is:

- If probing succeeds and no stored dimension exists, create vector tables for the probed dimension and persist it.
- If probing succeeds and the stored dimension matches, keep the existing vector tables.
- If probing succeeds and the stored dimension differs, recreate vector tables for the new dimension, clear stale embedding blobs, and persist the new dimension.
- If probing fails but a stored dimension exists, keep existing vector tables and continue using them.
- If probing fails and no stored dimension exists, run without vector search until embeddings become available.

### 4. No backward compatibility for old reduced embeddings

Backward compatibility with the old 256-dimensional Matryoshka storage is not required.

- The first startup after this change may recreate vector tables and clear stored embedding blobs if the probed model dimension differs from the persisted dimension.
- Textual memory data, graph relations, and other non-vector records remain intact.

## Consequences

### Positive

- Embedding quality is preserved for non-Matryoshka models and providers.
- Memory storage becomes provider-agnostic and uses the actual model output shape.
- Restarts are safe: vector tables are only recreated when the dimension actually changes.
- Temporary provider outages at startup no longer destroy the vector index.

### Trade-offs

- Startup now requires one probe embedding request when an embedder is available.
- Switching embedding models can invalidate stored vectors and requires index recreation.
- The vector table lifecycle moves from static SQL into storage initialization logic, which adds some implementation complexity.

### Implementation notes

- Core capability/provider changes: `core/llm/capabilities.py`, `core/llm/providers/openai_compatible.py`
- Memory changes: `sandbox/extensions/memory/main.py`, `sandbox/extensions/memory/storage.py`, `sandbox/extensions/memory/schema.sql`
- Embedding extension changes: `sandbox/extensions/embedding/main.py`, `sandbox/extensions/embedding/manifest.yaml`
- Documentation updates: `docs/memory.md`
