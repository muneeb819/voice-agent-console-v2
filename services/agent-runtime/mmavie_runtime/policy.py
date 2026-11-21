"""Campaign policy engine — the rules the Agent Runtime enforces in code (ADR 0005).

The LLM proposes; this engine decides. Every check returns a `Verdict` that the runtime
acts on and logs as a `PolicyDecision` session event, so each call carries evidence of
which rule allowed or blocked what.

What is enforced here, independent of anything the model says:
  * calling window in the lead's own time zone, before the call connects
  * stop / do-not-call phrases from the caller end the pitch immediately (DNC + end call)
  * every sentence the bot is about to speak passes the output filter (campaign rules plus
    built-in rules that block requests for SSN, card, bank or Medicare numbers)
  * caller facts only count if the quote the model cites really appears in what the
    caller said on this call (no invented ages, no invented consent)
  * transfer only when every required fact is caller-stated and consent was verified
  * dispositions only from the campaign's allowed list
  * call-length limit with a wrap-up nudge, then a fixed closing
  * required disclosures are fixed text, spoken verbatim, never LLM-generated

This is compliance tooling, not legal advice: what the rules should say for a campaign is
decided by the campaign's compliance owner.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mmavie_common.contracts.events import PolicyDecision
from mmavie_common.contracts.policy import CampaignPolicy

Action = Literal["allow", "block", "end_call", "play_disclosure", "set_disposition"]

CONSENT_FACT = "transfer_consent"

# Built-in output rules: always on, cannot be removed by campaign configuration.
BUILTIN_OUTPUT_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("builtin-no-ssn", re.compile(r"\b(social\s+security(\s+number)?|ssn)\b", re.I),
     "bot must not ask for or mention Social Security numbers"),
    ("builtin-no-card", re.compile(r"\b(credit|debit)\s+card\b|\bcard\s+number\b|\bcvv\b|\bexpiration\s+date\b", re.I),
     "bot must not ask for payment card details"),
    ("builtin-no-bank", re.compile(r"\b(bank\s+account|routing\s+number|account\s+number)\b", re.I),
     "bot must not ask for bank details"),
    ("builtin-no-medicare-id", re.compile(r"\b(medicare\s+(number|id|card\s+number)|\bmbi\b)", re.I),
     "bot must not ask for a Medicare number"),
    ("builtin-no-password", re.compile(r"\b(password|pin\s+number|one[-\s]time\s+code)\b", re.I),
     "bot must not ask for passwords or codes"),
]

CLOSING_LINE = "I'm sorry, I have to let you go now. Thank you for your time, and have a great day."


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    action: Action
    rule: str
    detail: str = ""
    disposition: str | None = None
    say: str | None = None  # fixed text the runtime must speak (disclosure / closing)

    def to_event(self, session_id: str, turn: int) -> PolicyDecision:
        return PolicyDecision(session_id=session_id, turn=turn, rule=self.rule, action=self.action,
                              detail=self.detail[:500])


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t.replace("'", ""))
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class CallState:
    """Per-call facts the engine needs. Owned by the runtime; one per call."""

    caller_turns: list[str] = field(default_factory=list)
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> {value, quote, turn}
    disclosures_played: set[str] = field(default_factory=set)
    ended: bool = False
    disposition: str | None = None


class PolicyEngine:
    def __init__(self, policy: CampaignPolicy):
        self.policy = policy
        self._stop = [_norm(p) for p in policy.stop_phrases if _norm(p)]
        self._rules = [(r.id, re.compile(r.pattern, re.I), r.reason) for r in policy.output_rules]

    # ------------------------------------------------------------ before connect
    def check_connect(self, lead_timezone: str, now_utc: datetime) -> Verdict:
        """Calling window in the lead's local time. Unknown time zone fails closed."""
        try:
            local = now_utc.astimezone(ZoneInfo(lead_timezone))
        except (ZoneInfoNotFoundError, ValueError):
            return Verdict(False, "block", "calling-window", f"unknown lead time zone '{lead_timezone}'")
        w = self.policy.calling_window
        ok_day = local.weekday() in w.days
        ok_time = w.start <= local.time().replace(tzinfo=None) < w.end
        if ok_day and ok_time:
            return Verdict(True, "allow", "calling-window", f"lead local time {local:%a %H:%M}")
        return Verdict(False, "block", "calling-window",
                       f"lead local time {local:%a %H:%M} outside {w.start:%H:%M}-{w.end:%H:%M} days {w.days}")

    # ------------------------------------------------------------ caller speech
    def on_caller_turn(self, state: CallState, text: str) -> Verdict:
        """Record what the caller said; a stop/DNC phrase ends the call with a DNC disposition."""
        state.caller_turns.append(text)
        said = f" {_norm(text)} "
        for phrase in self._stop:
            if f" {phrase} " in said:
                state.ended, state.disposition = True, "DNC"
                return Verdict(False, "end_call", "stop-phrase", f"caller said '{phrase}'", disposition="DNC",
                               say="I understand. I'll make sure you're not called again. Goodbye.")
        return Verdict(True, "allow", "caller-turn")

    # ------------------------------------------------------------ bot speech
    def filter_output(self, sentence: str) -> Verdict:
        """Run before every sentence is sent to TTS. A blocked sentence is never spoken."""
        for rule_id, rx, reason in BUILTIN_OUTPUT_RULES + self._rules:
            m = rx.search(sentence)
            if m:
                return Verdict(False, "block", rule_id, f"{reason}; matched '{m.group(0)}'")
        return Verdict(True, "allow", "output-filter")

    def disclosures(self, when: str, state: CallState) -> list[Verdict]:
        """Fixed disclosures due at this point that have not been played yet."""
        out = []
        for d in self.policy.disclosures:
            if d.when == when and d.id not in state.disclosures_played:
                out.append(Verdict(True, "play_disclosure", f"disclosure:{d.id}", f"when={when}", say=d.text))
        return out

    def mark_disclosure_played(self, state: CallState, disclosure_rule: str) -> None:
        state.disclosures_played.add(disclosure_rule.removeprefix("disclosure:"))

    # ------------------------------------------------------------ tools
    def record_caller_fact(self, state: CallState, name: str, value: str, quote: str) -> Verdict:
        """Accept a fact only if `quote` is words the caller actually said on this call."""
        q = _norm(quote)
        if len(q.split()) < 1:
            return Verdict(False, "block", "caller-fact", f"fact '{name}' has no supporting quote")
        for turn_no, said in enumerate(state.caller_turns):
            if f" {q} " in f" {_norm(said)} ":
                state.facts[name] = {"value": value, "quote": quote, "turn": turn_no}
                return Verdict(True, "allow", "caller-fact", f"{name}={value!r} supported by caller turn {turn_no}")
        return Verdict(False, "block", "caller-fact", f"quote for '{name}' not found in anything the caller said")

    def authorize_tool(self, state: CallState, name: str, args: dict[str, Any]) -> Verdict:
        if state.ended and name not in ("end_call", "set_disposition", "add_to_dnc"):
            return Verdict(False, "block", f"tool:{name}", "call is ending")
        if name == "transfer_to_live_agent":
            gate = self.policy.transfer
            missing = [f for f in gate.required_caller_facts if f not in state.facts]
            if missing:
                return Verdict(False, "block", "transfer-gate", f"caller has not stated: {missing}")
            if gate.require_caller_consent and CONSENT_FACT not in state.facts:
                return Verdict(False, "block", "transfer-gate", "caller consent to transfer not verified")
            return Verdict(True, "allow", "transfer-gate", "all required facts caller-stated and consent verified")
        if name == "set_disposition":
            code = str(args.get("disposition", "")).upper()
            if code not in self.policy.allowed_dispositions:
                return Verdict(False, "block", "disposition", f"'{code}' not in {self.policy.allowed_dispositions}")
            state.disposition = code
            return Verdict(True, "set_disposition", "disposition", code, disposition=code)
        if name == "add_to_dnc":
            state.disposition = "DNC"
            return Verdict(True, "allow", "dnc", "caller added to DNC", disposition="DNC")
        if name == "end_call":
            state.ended = True
            return Verdict(True, "end_call", "end-call", "requested by agent")
        return Verdict(True, "allow", f"tool:{name}", "custom tool")

    # ------------------------------------------------------------ time limit
    def time_check(self, state: CallState, elapsed_s: float, wrap_up_sent: bool) -> Verdict | None:
        """Wrap-up nudge at max(limit-20s, 60%); fixed closing and end at limit-6s."""
        limit = self.policy.max_call_seconds
        wrap_at = max(limit - 20, limit * 0.6)
        close_at = max(limit - 6, wrap_at + 1)
        if elapsed_s >= close_at and not state.ended:
            state.ended = True
            return Verdict(False, "end_call", "max-call-length", f"{elapsed_s:.0f}s of {limit}s", say=CLOSING_LINE)
        if elapsed_s >= wrap_at and not wrap_up_sent:
            left = int(limit - elapsed_s)
            return Verdict(True, "allow", "wrap-up",
                           f"TIME CHECK: about {left} seconds remain. Wrap up now: one short question at most, "
                           "then transfer if qualified and the caller agrees, otherwise close politely.")
        return None
