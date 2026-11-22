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


_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_AFFIRM = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "alright", "absolutely", "definitely", "please",
           "interested", "fine", "correct", "right"}
_AFFIRM_PHRASES = ("go ahead", "of course", "sounds good", "id like", "i would like", "i want", "connect me",
                   "transfer me", "put me through")
_NEGATE = {"no", "not", "dont", "never", "nope", "nah", "cant", "wont", "stop", "later", "wait", "isnt", "arent"}


def numbers_in(text: str) -> list[int]:
    """Integers in normalised text, from digits or English number words (0-999)."""
    out: list[int] = []
    words = _norm(text).split()
    i = 0
    while i < len(words):
        w = words[i]
        if w.isdigit():
            out.append(int(w))
        elif w in _ONES or w in _TENS:
            n = 0
            if w in _ONES and i + 1 < len(words) and words[i + 1] == "hundred":
                n, i = _ONES[w] * 100, i + 2
                if i < len(words) and words[i] == "and":
                    i += 1
                w = words[i] if i < len(words) else ""
            if w in _TENS:
                n += _TENS[w]
                if i + 1 < len(words) and words[i + 1] in _ONES and _ONES[words[i + 1]] < 10:
                    n, i = n + _ONES[words[i + 1]], i + 1
            elif w in _ONES:
                n += _ONES[w]
            else:
                i -= 1
            out.append(n)
        i += 1
    return out


def _is_affirmative(text: str) -> bool:
    t = _norm(text)
    words = set(t.split())
    return bool(words & _AFFIRM or any(p in t for p in _AFFIRM_PHRASES)) and not (words & _NEGATE)


def _is_negative(text: str) -> bool:
    return bool(set(_norm(text).split()) & _NEGATE)


_STOPWORDS = {"a", "an", "the", "and", "or", "of", "to", "i", "im", "my", "is", "am", "have", "has", "with", "only"}


def value_supported(value: str, turn: str) -> tuple[bool, str]:
    """Is `value` what the caller actually said in `turn`? Fails closed when unsure.

    * booleans (yes/no): the caller turn must be affirmative, or negative, without contradiction
    * numbers and ranges ("72", "70-79"): a number the caller said must equal / fall inside it
    * free text: every content word of the value must appear in the caller's turn
    """
    v = _norm(value)
    if v in ("yes", "true", "y"):
        return (_is_affirmative(turn), "caller turn is not a clear yes")
    if v in ("no", "false", "n"):
        return (_is_negative(turn) and not _is_affirmative(turn), "caller turn is not a clear no")
    said = numbers_in(turn)
    bounds = [int(x) for x in re.findall(r"\d+", value)]
    if bounds:
        lo, hi = (bounds[0], bounds[0]) if len(bounds) == 1 else (min(bounds[:2]), max(bounds[:2]))
        ok = any(lo <= n <= hi for n in said)
        return (ok, f"caller said {said or 'no number'}, not within {lo}-{hi}")
    missing = [w for w in v.split() if w not in _STOPWORDS and w not in set(_norm(turn).split())]
    return (not missing, f"caller did not say {missing}")


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
        """Accept a fact only if `quote` is words the caller said on this call AND the value is
        what that turn says (value_supported). Consent needs an affirmative, un-negated turn."""
        q = _norm(quote)
        if len(q.split()) < 1:
            return Verdict(False, "block", "caller-fact", f"fact '{name}' has no supporting quote")
        reason = "quote not found in anything the caller said"
        for turn_no, said in enumerate(state.caller_turns):
            if f" {q} " in f" {_norm(said)} ":
                # The quote is real; now the value must be what that turn actually says.
                check = "yes" if name == CONSENT_FACT else value
                ok, why = value_supported(check, said)
                if ok:
                    state.facts[name] = {"value": value, "quote": quote, "turn": turn_no}
                    return Verdict(True, "allow", "caller-fact", f"{name}={value!r} supported by caller turn {turn_no}")
                reason = why
        return Verdict(False, "block", "caller-fact", f"'{name}'={value!r} rejected: {reason}")

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
            if state.disposition == "DNC" and code != "DNC":
                return Verdict(False, "block", "disposition", f"DNC already recorded; '{code}' cannot replace it")
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
