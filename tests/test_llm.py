"""The local-model adapter: the two AgentDojo parsing bugs it fixes, and server errors that must not score."""
import pytest
from agentdojo.agent_pipeline.llms.local_llm import _parse_model_output
from agentdojo.functions_runtime import FunctionsRuntime

from agent.llm import ServerError, StrictLocalLLM, parse_completion

EMPTY_BODY = "<function=get_balance></function>"
STRAY_BRACE = '<function=send_money>{"recipient": "ZZ11", "amount": 10}}</function>'


def calls(message):
    return [(c.function, dict(c.args)) for c in message["tool_calls"]]


def test_empty_body_is_a_call_with_no_arguments():
    assert calls(parse_completion(EMPTY_BODY)) == [("get_balance", {})]


def test_trailing_text_after_the_json_is_ignored():
    assert calls(parse_completion(STRAY_BRACE)) == [("send_money", {"recipient": "ZZ11", "amount": 10})]


def test_agentdojo_stock_parser_drops_both_calls():
    # The behaviour the adapter replaces: both calls vanish and the turn reads as a final answer.
    assert not _parse_model_output(EMPTY_BODY)["tool_calls"]
    assert not _parse_model_output(STRAY_BRACE)["tool_calls"]


def test_text_without_a_call_is_a_final_answer():
    assert calls(parse_completion("Your balance is 100.")) == []


def test_unreadable_call_is_not_executed():
    assert calls(parse_completion("<function=send_money>{recipient: ZZ11</function>")) == []


def test_server_error_raises_instead_of_an_empty_answer():
    llm = StrictLocalLLM("http://127.0.0.1:9/v1", "m", 8192, timeout_s=2)
    with pytest.raises(ServerError):
        llm.query("hi", FunctionsRuntime([]), messages=[
            {"role": "system", "content": [{"type": "text", "content": "s"}]},
            {"role": "user", "content": [{"type": "text", "content": "hi"}]}])
