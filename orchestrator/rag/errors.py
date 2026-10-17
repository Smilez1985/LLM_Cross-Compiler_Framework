#!/usr/bin/env python3
"""Errors of the knowledge base. Messages are shown to users and MCP clients, so they say what to do."""


class KnowledgeBaseError(RuntimeError):
    """The knowledge base cannot serve the request (storage, configuration or embedding problem)."""


class EmbeddingError(KnowledgeBaseError):
    """The embedding model failed or returned something unusable."""


class EmbeddingModelNotCached(EmbeddingError):
    """Offline mode is on and the embedding model has not been downloaded yet."""
