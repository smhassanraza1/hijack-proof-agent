"""Local model element for AgentDojo pipelines (replaces AgentDojo's LocalLLM; MIT, THIRD_PARTY_NOTICES.md).

AgentDojo's bundled LocalLLM swallows server errors and returns an empty answer, which an
eval would score as "the attack failed". Its parser also drops a tool call whose body is
empty (`<function=get_balance></function>`) or has trailing text after the JSON (a stray
`}`). This element raises on server errors, parses the first JSON object of a call (an
empty body is `{}`), turns thinking off per request, flags calls whose prompt may have been
cut to fit the server's context window, and logs latency, token usage and malformed tool calls per call in
`calls`, which the runner reads and clears per task (AgentDojo's task runner drops
`extra_args`).
"""
import json
import re
import time
from collections.abc import Sequence

import openai
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.llms.local_llm import _make_system_prompt
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionCall, FunctionsRuntime
from agentdojo.types import ChatAssistantMessage, ChatMessage, get_text_content_as_str, text_content_block_from_string

_CALL_OPEN = re.compile(r"<function\s*=\s*([^>]+)>")


class ServerError(Exception): ...


def parse_completion(completion: str) -> ChatAssistantMessage:
    """Reads the first `<function=name>{json}</function>` call; text without one is a final answer."""
    message = ChatAssistantMessage(role="assistant", content=[text_content_block_from_string(completion.strip())],
                                   tool_calls=[])
    match = _CALL_OPEN.search(completion)
    if not match:
        return message
    body = completion[match.end():].split("</function>", 1)[0].strip()
    try:
        args = json.JSONDecoder().raw_decode(body)[0] if body else {}
    except json.JSONDecodeError:
        return message
    if isinstance(args, dict):
        message["tool_calls"] = [FunctionCall(function=match.group(1).strip(), args=args)]
    return message


def _flatten(content) -> str:
    if content is None or isinstance(content, str):
        return content or ""
    return "\n\n".join(get_text_content_as_str([block]) for block in content).strip()


class StrictLocalLLM(BasePipelineElement):
    def __init__(self, base_url: str, model: str, context_tokens: int, max_tokens: int = 1024, timeout_s: float = 300):
        self.client = openai.OpenAI(api_key="EMPTY", base_url=base_url, timeout=timeout_s, max_retries=0)
        self.model = model
        self.context_tokens = context_tokens
        self.max_tokens = max_tokens
        self.name = "local"
        self.calls: list[dict] = []

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ):
        wire = []
        for m in messages:
            role, content = m["role"], m["content"]
            if role == "system":
                content = _make_system_prompt(_flatten(content), runtime.functions.values())
            elif role == "tool":
                if m.get("error"):
                    content = json.dumps({"error": m["error"]})
                else:
                    result = _flatten(content)
                    content = json.dumps({"result": "Success" if result == "None" else result})
            else:
                content = _flatten(content)
            wire.append({"role": role, "content": content})

        start = time.monotonic()
        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=wire, temperature=0.0, max_tokens=self.max_tokens,
                extra_body={"reasoning_effort": "none"},
            )
        except openai.OpenAIError as exc:
            raise ServerError(f"{type(exc).__name__}: {exc}") from exc
        latency = time.monotonic() - start

        choice = resp.choices[0]
        completion = choice.message.content
        if completion is None:
            raise ServerError("empty completion")
        output = parse_completion(completion)
        malformed = "<function" in completion and not output["tool_calls"]

        usage = resp.usage
        self.calls.append({
            "model": resp.model,
            "latency_s": round(latency, 3),
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "cached_tokens": getattr(usage.prompt_tokens_details, "cached_tokens", 0) if usage.prompt_tokens_details else 0,
            "finish_reason": choice.finish_reason,
            "context_full": usage.prompt_tokens + self.max_tokens > self.context_tokens,
            "malformed_tool_call": malformed,
            "completion": completion,
        })
        return query, runtime, env, [*messages, output], extra_args
