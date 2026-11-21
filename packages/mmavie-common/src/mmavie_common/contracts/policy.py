"""Campaign policy: rules the Agent Runtime ENFORCES in code on every turn and tool call.

These are not prompt text. The runtime evaluates them independently of what the LLM says
(implementation lands in Phase 4). Prompt guidance may repeat them; enforcement never
depends on it.
"""
from __future__ import annotations

import re
from datetime import time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

DisclosureWhen = Literal["opening", "before_transfer", "on_request"]


class RequiredDisclosure(BaseModel):
    """Fixed text spoken verbatim (not LLM-generated); playback is logged as evidence."""

    id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    when: DisclosureWhen
    text: str = Field(min_length=1, max_length=600)


class CallingWindow(BaseModel):
    """Allowed local calling hours, evaluated in the lead's time zone before connecting."""

    start: time = time(8, 0)
    end: time = time(21, 0)
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5], description="0=Mon … 6=Sun")

    @model_validator(mode="after")
    def _check(self) -> CallingWindow:
        if self.end <= self.start:
            raise ValueError("calling window end must be after start")
        if not self.days or any(d < 0 or d > 6 for d in self.days):
            raise ValueError("days must be 0-6")
        return self


class OutputRule(BaseModel):
    """Regex applied to each LLM sentence BEFORE it is spoken. A match blocks the sentence."""

    id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    pattern: str = Field(min_length=1, max_length=500)
    reason: str = Field(max_length=200)

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, p: str) -> str:
        try:
            re.compile(p, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        return p


class TransferGate(BaseModel):
    """Transfer is refused until each listed fact has been stated by the caller."""

    required_caller_facts: list[str] = Field(default_factory=list, max_length=16)
    require_caller_consent: bool = True


class CampaignPolicy(BaseModel):
    campaign_id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    version: int = Field(ge=1)
    max_concurrent_calls: int = Field(default=1, ge=1, le=500)
    max_call_seconds: int = Field(default=600, ge=30, le=7200)
    calling_window: CallingWindow = Field(default_factory=CallingWindow)
    stop_phrases: list[str] = Field(
        default_factory=lambda: ["stop calling", "do not call", "don't call", "remove me", "take me off"],
        max_length=64,
    )
    disclosures: list[RequiredDisclosure] = Field(default_factory=list, max_length=8)
    output_rules: list[OutputRule] = Field(default_factory=list, max_length=64)
    transfer: TransferGate = Field(default_factory=TransferGate)
    allowed_dispositions: list[str] = Field(default_factory=lambda: ["XFER", "NI", "DNC", "DQ", "CALLBK", "LANG"])

    @field_validator("stop_phrases")
    @classmethod
    def _normalise(cls, phrases: list[str]) -> list[str]:
        return [p.strip().lower() for p in phrases if p.strip()]
