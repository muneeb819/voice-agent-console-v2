"""Session events emitted per call. They feed CDRs, latency metrics, QA and compliance audit.

Every latency figure in MMA-VIE reports is derived from these events, so a benchmark can
always be traced back to the call and turn that produced it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class _Base(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    ts: datetime = Field(default_factory=_now)
    turn: int = Field(default=0, ge=0)


class CallStarted(_Base):
    type: Literal["call_started"] = "call_started"
    campaign_id: str
    agent_id: str
    agent_version: int
    transport: Literal["sip", "audiosocket", "browser", "synthetic"]


class TranscriptFinal(_Base):
    type: Literal["transcript_final"] = "transcript_final"
    text: str
    stt_model: str
    end_of_speech_to_final_ms: float = Field(ge=0)


class LlmTurn(_Base):
    type: Literal["llm_turn"] = "llm_turn"
    llm_model: str
    time_to_first_token_ms: float = Field(ge=0)
    output_tokens: int = Field(ge=0)


class TtsStarted(_Base):
    type: Literal["tts_started"] = "tts_started"
    tts_model: str
    time_to_first_audio_ms: float = Field(ge=0)
    end_of_speech_to_first_audio_ms: float = Field(ge=0, description="Caller stops talking -> bot audio starts")


class BargeIn(_Base):
    type: Literal["barge_in"] = "barge_in"
    speech_to_silence_ms: float = Field(ge=0, description="Caller speech detected -> bot audio stopped")


class PolicyDecision(_Base):
    type: Literal["policy_decision"] = "policy_decision"
    rule: str
    action: Literal["allow", "block", "end_call", "play_disclosure", "set_disposition"]
    detail: str = ""


class ToolCall(_Base):
    type: Literal["tool_call"] = "tool_call"
    name: str
    executed: bool
    result: str = ""


class CallEnded(_Base):
    type: Literal["call_ended"] = "call_ended"
    reason: str
    disposition: str | None = None
    duration_s: float = Field(ge=0)


SessionEvent = Annotated[
    Union[CallStarted, TranscriptFinal, LlmTurn, TtsStarted, BargeIn, PolicyDecision, ToolCall, CallEnded],
    Field(discriminator="type"),
]
