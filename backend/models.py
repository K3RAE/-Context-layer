"""
ContextLayer — Pydantic Models
==============================

Defines every request and response schema used by the ContextLayer FastAPI
backend.  All models use strict Pydantic v2 validation so malformed payloads
are rejected before they reach any business logic.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared / primitive models
# ---------------------------------------------------------------------------

class Message(BaseModel):
    """A single chat message with a role (e.g. 'user', 'assistant') and content."""

    role: str = Field(..., description="The role of the message sender, e.g. 'user' or 'assistant'.")
    content: str = Field(..., description="The text content of the message.")


# ---------------------------------------------------------------------------
# /compress
# ---------------------------------------------------------------------------

class CompressRequest(BaseModel):
    """Request body for the /compress endpoint."""

    messages: list[dict[str, Any]] = Field(
        ...,
        description="List of message dicts, each containing at least 'role' and 'content' keys.",
    )
    preserve_last_n: int = Field(
        default=4,
        ge=0,
        description="Number of most-recent messages to keep verbatim (not compressed).",
    )


class CompressResponse(BaseModel):
    """Response from the /compress endpoint."""

    compressed_messages: list[dict[str, Any]] = Field(
        ..., description="The resulting messages after compression."
    )
    original_tokens: int = Field(..., description="Token count of the original conversation.")
    compressed_tokens: int = Field(..., description="Token count after compression.")
    saved_percent: float = Field(
        ..., description="Percentage of tokens saved (0–100)."
    )
    method: str = Field(
        ...,
        description="Compression method used: 'llmlingua2' or 'rule_based'.",
    )
    preserve_last_n: int = Field(
        ..., description="How many recent messages were preserved verbatim."
    )


# ---------------------------------------------------------------------------
# /handoff
# ---------------------------------------------------------------------------

class HandoffRequest(BaseModel):
    """Request body for the /handoff endpoint."""

    messages: list[dict[str, Any]] = Field(
        ..., description="Full conversation history to summarise for handoff."
    )
    session_id: str = Field(
        ..., description="Unique session identifier for the conversation."
    )


class HandoffStructured(BaseModel):
    """Structured extraction of conversation context for handoff."""

    task: str = Field(default="", description="High-level task description.")
    decisions: list[str] = Field(default_factory=list, description="Decisions already made.")
    current_state: str = Field(default="", description="Current state of the task.")
    constraints: list[str] = Field(default_factory=list, description="Active constraints or requirements.")
    rejected_approaches: list[str] = Field(
        default_factory=list, description="Approaches that were tried and rejected."
    )
    next_step: str = Field(default="", description="The next action to take.")
    key_files_or_code: list[str] = Field(
        default_factory=list, description="Important files or code snippets referenced."
    )
    tone: str = Field(default="neutral", description="Conversational tone, e.g. 'formal', 'casual'.")


class HandoffResponse(BaseModel):
    """Response from the /handoff endpoint."""

    structured: HandoffStructured = Field(
        ..., description="Structured handoff data extracted from the conversation."
    )
    handoff_note: str = Field(
        ..., description="Human-readable handoff note summarising the conversation."
    )
    token_count: int = Field(
        ..., description="Token count of the generated handoff note."
    )
    method: str = Field(
        default="ollama",
        description="Method used to generate the handoff: 'ollama' or 'rule_based'.",
    )


# ---------------------------------------------------------------------------
# /remember  &  /recall
# ---------------------------------------------------------------------------

class RememberRequest(BaseModel):
    """Request body for the /remember endpoint."""

    session_id: str = Field(..., description="Session to associate facts with.")
    messages: list[dict[str, Any]] = Field(
        ..., description="Conversation messages to extract knowledge from."
    )
    source_llm: str = Field(
        ..., description="Name of the LLM that produced these messages (e.g. 'gpt-4o')."
    )


class RememberResponse(BaseModel):
    """Response from the /remember endpoint."""

    facts_stored: int = Field(..., description="Number of fact triples stored.")
    entities: list[str] = Field(default_factory=list, description="Unique entities extracted.")
    session_id: str = Field(..., description="The session these facts belong to.")


class RecallRequest(BaseModel):
    """Request body for the /recall endpoint."""

    query: str = Field(..., description="Natural-language query to search stored knowledge.")
    session_id: Optional[str] = Field(
        default=None, description="Optionally limit search to a specific session."
    )
    top_k: int = Field(
        default=5, ge=1, description="Maximum number of facts to return."
    )


class RecallResponse(BaseModel):
    """Response from the /recall endpoint."""

    relevant_context: str = Field(
        ..., description="Human-readable summary of the most relevant stored facts."
    )
    facts: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw fact triples returned by the search."
    )
    source_sessions: list[str] = Field(
        default_factory=list, description="Session IDs that contributed facts."
    )


# ---------------------------------------------------------------------------
# /health  &  /memory (DELETE)
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Response from the /health endpoint."""

    status: str = Field(..., description="Overall health status, e.g. 'ok' or 'degraded'.")
    ollama: bool = Field(..., description="Whether the Ollama server is reachable.")
    ollama_models: list[str] = Field(
        default_factory=list, description="Models available on the Ollama server."
    )
    llmlingua: bool = Field(..., description="Whether LLMLingua-2 is available.")
    graphiti: bool = Field(
        default=False, description="Whether the Graphiti knowledge-graph backend is available."
    )
    memory_db: str = Field(..., description="Path to the SQLite memory database.")
    uptime_seconds: float = Field(..., description="Seconds since the backend started.")


class DeleteMemoryResponse(BaseModel):
    """Response from DELETE /memory."""

    deleted: bool = Field(..., description="Whether the memory was successfully cleared.")
    message: str = Field(..., description="Human-readable status message.")
