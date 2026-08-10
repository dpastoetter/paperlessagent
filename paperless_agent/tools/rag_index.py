"""Chunking, embedding, and Chroma-backed retrieval for RAG."""

from __future__ import annotations

import re
from typing import Any

import chromadb
import httpx

from paperless_agent import config
from paperless_agent.config import (
    CHROMA_DIR,
    CHUNK_OVERLAP_CHARS,
    CHUNK_SIZE_CHARS,
    RETRIEVE_TOP_K,
    ensure_data_dirs,
)
from paperless_agent.ollama_setup import format_http_error
from paperless_agent.tools.metadata_db import get_document, mark_indexed


def _chroma_collection():
    ensure_data_dirs()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name="paperless_chunks",
        metadata={"hnsw:space": "cosine"},
    )


def _embed_gemini(texts: list[str]) -> list[list[float]]:
    from google import genai

    if not config.GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    vectors: list[list[float]] = []
    for text in texts:
        response = client.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=text,
        )
        embeddings = getattr(response, "embeddings", None)
        if embeddings:
            vectors.append(list(embeddings[0].values))
            continue
        embedding = getattr(response, "embedding", None)
        if embedding is not None:
            vectors.append(list(embedding.values))
            continue
        raise RuntimeError("Unexpected embedding response shape from Gemini API")
    return vectors


def _embed_openai(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    from paperless_agent.auth import resolve_auth_mode, resolve_openai_api_key

    # Platform embeddings need an API key. ChatGPT OAuth alone uses local embedder.
    if resolve_auth_mode() != "api_key" or not resolve_openai_api_key():
        return _embed_local(texts)

    from paperless_agent.auth import ensure_openai_env

    ensure_openai_env()
    client = OpenAI()
    response = client.embeddings.create(model=config.EMBEDDING_MODEL, input=texts)
    ordered = sorted(response.data, key=lambda item: item.index)
    return [list(item.embedding) for item in ordered]


def _embed_ollama(texts: list[str]) -> list[list[float]]:
    """Embeddings via a local Ollama server (/api/embed, batch input)."""
    model = config.EMBEDDING_MODEL
    try:
        resp = httpx.post(
            f"{config.OLLAMA_BASE_URL}/api/embed",
            json={"model": model, "input": texts},
            timeout=120,
        )
        resp.raise_for_status()
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {config.OLLAMA_BASE_URL} — is `ollama serve` running?"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(format_http_error(exc, model=model, kind="embedding model")) from exc
    embeddings = resp.json().get("embeddings")
    if not embeddings or len(embeddings) != len(texts):
        raise RuntimeError("Unexpected embedding response shape from Ollama")
    return [list(vec) for vec in embeddings]


def _embed_local(texts: list[str]) -> list[list[float]]:
    """Deterministic local embeddings when no Platform API key is available."""
    vectors: list[list[float]] = []
    dims = 64
    for text in texts:
        vec = [0.0] * dims
        data = (text or "").encode("utf-8")
        if not data:
            vectors.append(vec)
            continue
        for i, byte in enumerate(data):
            vec[i % dims] += ((byte % 31) + 1) / 31.0
        # L2 normalize
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        vectors.append([v / norm for v in vec])
    return vectors



def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Split text into overlapping character chunks, preferring paragraph breaks."""
    cleaned = re.sub(r"\s+\n", "\n", (text or "").strip())
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    length = len(cleaned)
    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            # Prefer splitting on paragraph or sentence boundary
            window = cleaned[start:end]
            split_at = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if split_at > chunk_size // 3:
                end = start + split_at + 1
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(0, end - overlap)
    return chunks


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts with the configured provider (Gemini, OpenAI, or Ollama)."""
    if not texts:
        return []
    if config.LLM_PROVIDER == "openai":
        return _embed_openai(texts)
    if config.LLM_PROVIDER == "ollama":
        return _embed_ollama(texts)
    return _embed_gemini(texts)

def index_document(
    document_id: str,
    text: str,
    filename: str | None = None,
    doc_type: str | None = None,
) -> dict[str, Any]:
    """
    Chunk document text, embed chunks, and upsert into the local Chroma store.
    """
    meta = get_document(document_id)
    if meta.get("status") != "success":
        return meta

    document = meta["document"]
    filename = filename or document.get("filename") or "document"
    doc_type = doc_type or document.get("doc_type") or "other"
    chunks = chunk_text(text or document.get("summary") or "")
    if not chunks:
        # Index at least the summary/filename so the doc is findable
        fallback = " ".join(
            filter(
                None,
                [
                    document.get("summary"),
                    document.get("counterparties"),
                    filename,
                    doc_type,
                ],
            )
        )
        chunks = chunk_text(fallback) or [f"{filename} ({doc_type})"]

    try:
        vectors = embed_texts(chunks)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"embedding failed: {exc}"}

    collection = _chroma_collection()
    ids = [f"{document_id}::chunk::{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "document_id": document_id,
            "filename": filename,
            "doc_type": doc_type,
            "chunk_index": i,
        }
        for i in range(len(chunks))
    ]

    # Replace prior chunks for this document
    existing = collection.get(where={"document_id": document_id})
    if existing and existing.get("ids"):
        collection.delete(ids=existing["ids"])

    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=vectors,
        metadatas=metadatas,
    )
    mark_indexed(document_id)
    return {
        "status": "success",
        "document_id": document_id,
        "chunk_count": len(chunks),
    }


def retrieve_chunks(
    query: str,
    top_k: int = RETRIEVE_TOP_K,
    doc_type: str | None = None,
) -> dict[str, Any]:
    """Semantic retrieval over indexed document chunks."""
    if not query or not query.strip():
        return {"status": "error", "error": "query must not be empty"}

    try:
        query_vec = embed_texts([query.strip()])[0]
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"embedding failed: {exc}"}

    collection = _chroma_collection()
    kwargs: dict[str, Any] = {
        "query_embeddings": [query_vec],
        "n_results": max(1, min(top_k, 20)),
        "include": ["documents", "metadatas", "distances"],
    }
    if doc_type:
        kwargs["where"] = {"doc_type": doc_type}

    try:
        result = collection.query(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"chroma query failed: {exc}"}

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    ids = (result.get("ids") or [[]])[0]

    chunks = []
    for i, doc in enumerate(documents):
        meta = metadatas[i] if i < len(metadatas) else {}
        distance = distances[i] if i < len(distances) else None
        chunks.append(
            {
                "chunk_id": ids[i] if i < len(ids) else None,
                "text": doc,
                "document_id": meta.get("document_id"),
                "filename": meta.get("filename"),
                "doc_type": meta.get("doc_type"),
                "chunk_index": meta.get("chunk_index"),
                "distance": distance,
            }
        )

    return {"status": "success", "count": len(chunks), "chunks": chunks}
