"""Agent definitions with genuine per-agent model selection.

An Agent has immutable, numbered AgentVersions. Calls always run a specific published
version, so a change in the console never alters a call already in progress, and any
version can be rolled back to.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ModelKind = Literal["stt", "llm", "tts", "vad", "clone"]
VersionStatus = Literal["draft", "published", "retired"]


class ModelPin(BaseModel):
    """A model alias routed by the MMA AI Gateway, optionally pinned to a registry revision."""

    alias: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")
    revision: str | None = Field(default=None, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)


class VoicePin(BaseModel):
    """A TTS voice: a stock voice name, or 'clone:<id>' for a consented cloned voice."""

    voice: str = Field(min_length=1, max_length=80, pattern=r"^(clone:[0-9a-f]{32}|[A-Za-z0-9_.-]+)$")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class AgentModels(BaseModel):
    stt: ModelPin
    llm: ModelPin
    tts: ModelPin
    voice: VoicePin
    # Ordered fallbacks the Gateway may use if the primary alias has no healthy capacity.
    llm_fallbacks: list[ModelPin] = Field(default_factory=list, max_length=3)


class FunctionSpec(BaseModel):
    """A tool the LLM may call. Built-in names are executed by the Agent Runtime."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(max_length=1000)
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    builtin: bool = False


BUILTIN_FUNCTIONS = {"transfer_to_live_agent", "set_disposition", "add_to_dnc", "end_call"}


class AgentVersion(BaseModel):
    agent_id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    version: int = Field(ge=1)
    status: VersionStatus = "draft"
    name: str = Field(min_length=1, max_length=120)
    language: str = Field(default="en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    persona_prompt: str = Field(min_length=1, max_length=25000)
    opening: str = Field(default="", max_length=600)
    models: AgentModels
    functions: list[FunctionSpec] = Field(default_factory=list, max_length=32)
    created_at: datetime | None = None
    created_by: str | None = None

    @field_validator("functions")
    @classmethod
    def _unique_and_builtin_flags(cls, fns: list[FunctionSpec]) -> list[FunctionSpec]:
        names = [f.name for f in fns]
        if len(names) != len(set(names)):
            raise ValueError("function names must be unique")
        for f in fns:
            if f.builtin and f.name not in BUILTIN_FUNCTIONS:
                raise ValueError(f"'{f.name}' is not a built-in function")
        return fns
