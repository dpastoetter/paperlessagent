"""ADK entrypoint: root_agent answers questions over archived documents via RAG."""

from google.adk.agents import Agent

from paperless_agent.llm import get_model
from paperless_agent.tools.metadata_db import get_document, search_metadata
from paperless_agent.tools.rag_index import retrieve_chunks


def build_query_agent() -> Agent:
    """Build a fresh query agent with the current auth/model settings."""
    return Agent(
        model=get_model(),
        name="paperless_query",
        description=(
            "Answers questions about archived paper documents using metadata "
            "search and semantic RAG retrieval."
        ),
        instruction=(
            "You are a local paperless archive assistant.\n"
            "When the user asks a question about their documents:\n"
            "1. Call retrieve_chunks with their question for semantic matches.\n"
            "2. Optionally call search_metadata for structured filters "
            "(doc_type, counterparty, dates, keywords).\n"
            "3. Use get_document when you need full metadata for a document_id.\n"
            "4. Answer clearly using only retrieved evidence. Cite filename and "
            "document_id for each claim. If evidence is weak or missing, say so.\n"
            "Prefer concise answers with a short Sources section."
        ),
        tools=[retrieve_chunks, search_metadata, get_document],
    )


root_agent = build_query_agent()
