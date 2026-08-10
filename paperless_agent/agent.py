"""ADK entrypoint: root_agent is the document ingest pipeline."""

from paperless_agent.pipeline.agents import build_pipeline_agent

root_agent = build_pipeline_agent()
