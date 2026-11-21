from datetime import UTC, datetime

import pytest

from mmavie_common.contracts.policy import CampaignPolicy
from mmavie_runtime.policy import CONSENT_FACT, CallState, PolicyEngine


def engine(**kw) -> PolicyEngine:
    base = {"campaign_id": "fetest", "version": 1}
    base.update(kw)
    return PolicyEngine(CampaignPolicy.model_validate(base))


# ------------------------------------------------------------ calling window
@pytest.mark.parametrize("utc,tz,allowed", [
    ("2026-11-23T15:00:00", "America/New_York", True),     # Mon 10:00 local
    ("2026-11-23T12:30:00", "America/New_York", False),    # Mon 07:30 local, before 08:00
    ("2026-11-24T02:30:00", "America/Los_Angeles", True),  # Mon 18:30 local (Tue in UTC)
    ("2026-11-24T02:30:00", "America/New_York", False),    # Mon 21:30 local, after 21:00
    ("2026-11-29T17:00:00", "America/Chicago", False),     # Sunday
])
def test_calling_window_uses_lead_local_time(utc, tz, allowed):
    now = datetime.fromisoformat(utc).replace(tzinfo=UTC)
    v = engine().check_connect(tz, now)
    assert v.allowed is allowed, v.detail


def test_unknown_timezone_fails_closed():
    v = engine().check_connect("Mars/Olympus", datetime(2026, 11, 23, 15, tzinfo=UTC))
    assert not v.allowed and "unknown" in v.detail


# ------------------------------------------------------------ stop / DNC
@pytest.mark.parametrize("text", ["Please STOP CALLING me.", "don't call me again", "Remove me from your list!",
                                  "Take me off your list right now"])
def test_stop_phrases_end_call_with_dnc(text):
    e, s = engine(), CallState()
    v = e.on_caller_turn(s, text)
    assert v.action == "end_call" and v.disposition == "DNC" and s.ended and v.say


def test_stop_phrase_matching_is_whole_words_and_deliberately_cautious():
    # Partial words never match.
    assert engine().on_caller_turn(CallState(), "the bus stopped, calling from outside").allowed
    # Phrase matching is not semantic: a stop phrase inside another sentence still ends the call.
    # This errs on the side of the caller (an unwanted DNC costs a lead, a missed one is a violation).
    assert engine().on_caller_turn(CallState(), "I meant to stop calling my bank").action == "end_call"


# ------------------------------------------------------------ output filter
@pytest.mark.parametrize("sentence", [
    "Can I get your social security number?", "What's your SSN?", "Read me the card number please.",
    "And your bank account and routing number?", "What is your Medicare number?", "Can you confirm your MBI?",
])
def test_builtin_rules_block_sensitive_requests(sentence):
    v = engine().filter_output(sentence)
    assert not v.allowed and v.rule.startswith("builtin-")


def test_campaign_rule_blocks_and_clean_sentence_passes():
    e = engine(output_rules=[{"id": "no-gov", "pattern": r"\b(medicare|government)[- ](approved|official)\b",
                              "reason": "no affiliation claims"}])
    assert e.filter_output("This is a Medicare-approved program.").rule == "no-gov"
    assert e.filter_output("A licensed specialist can explain your options.").allowed


# ------------------------------------------------------------ caller facts + transfer gate
def gate_engine():
    return engine(transfer={"required_caller_facts": ["age_band", "interest"], "require_caller_consent": True})


def test_fact_rejected_when_quote_not_said_by_caller():
    e, s = gate_engine(), CallState()
    e.on_caller_turn(s, "Hi, who is this?")
    v = e.record_caller_fact(s, "age_band", "70-79", "I turned seventy two")
    assert not v.allowed and "age_band" not in s.facts


def test_transfer_blocked_until_facts_and_consent_are_caller_stated():
    e, s = gate_engine(), CallState()
    assert not e.authorize_tool(s, "transfer_to_live_agent", {}).allowed
    e.on_caller_turn(s, "Yes, I turned seventy-two in March.")
    e.on_caller_turn(s, "I'd like to look at some options.")
    assert e.record_caller_fact(s, "age_band", "70-79", "I turned seventy-two").allowed
    assert e.record_caller_fact(s, "interest", "yes", "look at some options").allowed
    v = e.authorize_tool(s, "transfer_to_live_agent", {})
    assert not v.allowed and "consent" in v.detail
    e.on_caller_turn(s, "Sure, go ahead and connect me.")
    assert e.record_caller_fact(s, CONSENT_FACT, "yes", "go ahead and connect me").allowed
    assert e.authorize_tool(s, "transfer_to_live_agent", {}).allowed


# ------------------------------------------------------------ dispositions, end
def test_disposition_allowlist():
    e, s = engine(), CallState()
    assert not e.authorize_tool(s, "set_disposition", {"disposition": "SALE"}).allowed
    v = e.authorize_tool(s, "set_disposition", {"disposition": "ni"})
    assert v.allowed and s.disposition == "NI"


def test_no_transfer_after_call_is_ending():
    e, s = engine(transfer={"required_caller_facts": [], "require_caller_consent": False}), CallState()
    e.on_caller_turn(s, "do not call me")
    assert not e.authorize_tool(s, "transfer_to_live_agent", {}).allowed
    assert e.authorize_tool(s, "add_to_dnc", {}).allowed


# ------------------------------------------------------------ disclosures, time limit, events
def test_disclosures_fixed_text_once():
    e, s = engine(disclosures=[{"id": "ai", "when": "opening", "text": "This call uses an automated assistant."}]), CallState()
    (d,) = e.disclosures("opening", s)
    assert d.say == "This call uses an automated assistant."
    e.mark_disclosure_played(s, d.rule)
    assert e.disclosures("opening", s) == []


def test_time_limit_wrap_up_then_close():
    e, s = engine(max_call_seconds=120), CallState()
    assert e.time_check(s, 50, False) is None
    wrap = e.time_check(s, 100, False)
    assert wrap and wrap.allowed and "TIME CHECK" in wrap.detail
    assert e.time_check(s, 105, True) is None
    close = e.time_check(s, 115, True)
    assert close and close.action == "end_call" and close.say and s.ended


def test_verdict_becomes_session_event():
    v = engine().filter_output("What's your SSN?")
    ev = v.to_event("sess-12345678", 3)
    assert ev.type == "policy_decision" and ev.action == "block" and ev.turn == 3
