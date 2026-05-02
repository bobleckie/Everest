# AI agent service — delegates all calls to orchestrator_agent._call_ai
# which uses the DB-configured provider (Anthropic/OpenAI) with retry logic.
#
# This module was originally an OpenAI-only shim that fell back to mock
# responses when no OpenAI key was set. Mock responses are not acceptable —
# every function here MUST call the real AI provider or raise an error.

import logging

logger = logging.getLogger(__name__)


def run_agent(prompt: str, system: str = "You are a helpful AI assistant for government RFP proposal preparation.") -> str:
    """Call the configured AI provider. Raises on failure instead of
    returning fake data."""
    from .orchestrator_agent import _call_ai
    result = _call_ai(prompt, system)
    if result and result.startswith("[MOCK RESPONSE"):
        raise RuntimeError(
            "No AI provider is configured or reachable. "
            "Check Settings → AI Configuration to set an API key."
        )
    return result

# Specific functions for personas
def competitive_research(query: str) -> str:
    prompt = f"As a competitive research agent, analyze the market for: {query}"
    return run_agent(prompt)

def parsons_sme(query: str) -> str:
    prompt = f"As Parsons SME, provide information on Parsons offerings, staff, technology, security for: {query}"
    return run_agent(prompt)

def conflict_detector(content: str) -> str:
    prompt = f"Check for conflicts in this content: {content}. Respond with 'No conflicts detected' or list any conflicts found."
    return run_agent(prompt)

def requirement_extractor(content: str) -> str:
    prompt = f"Extract all requirements, questions, submission requests, and deliverables from this RFP content: {content}. List them clearly and comprehensively."
    return run_agent(prompt)

def document_composer(query: str) -> str:
    prompt = (
        "As a proposal document composer, produce a clear, well-structured draft response. "
        "Use headings, bullet points where helpful, and keep the tone professional and confident. "
        f"Task: {query}"
    )
    return run_agent(prompt)