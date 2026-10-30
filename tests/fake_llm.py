"""A scripted stand-in for the model: plays planner, judge and agent; records every judge prompt."""
from collections.abc import Sequence

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.functions_runtime import FunctionCall
from agentdojo.types import ChatAssistantMessage, get_text_content_as_str, text_content_block_from_string


class FakeLLM(BasePipelineElement):
    def __init__(self, plan_prompt: str, judge_prompt: str, plan: str, steps: list, verdicts: list | None = None):
        self.plan_prompt, self.judge_prompt = plan_prompt, judge_prompt
        self.plan, self.steps, self.verdicts = plan, list(steps), verdicts
        self.judge_prompts: list[str] = []
        self.agent_turns = 0

    def query(self, query, runtime, env=None, messages: Sequence = (), extra_args={}):
        system = get_text_content_as_str(messages[0]["content"])
        user = get_text_content_as_str(messages[1]["content"])
        if system == self.plan_prompt:
            text, calls = self.plan, []
        elif system == self.judge_prompt:
            if self.verdicts is None:
                raise AssertionError(f"judge called but should not be:\n{user}")
            self.judge_prompts.append(user)
            text, calls = self.verdicts.pop(0), []
        else:
            self.agent_turns += 1
            step = self.steps.pop(0) if self.steps else "Done."
            if isinstance(step, tuple):
                text, calls = "", [FunctionCall(function=step[0], args=step[1], id=f"c{self.agent_turns}")]
            else:
                text, calls = step, []
        out = ChatAssistantMessage(role="assistant", content=[text_content_block_from_string(text)], tool_calls=calls)
        return query, runtime, env, [*messages, out], extra_args


def tool_outputs(messages):
    return [(m["tool_call"].function, m["error"] or get_text_content_as_str(m["content"]))
            for m in messages if m["role"] == "tool"]
