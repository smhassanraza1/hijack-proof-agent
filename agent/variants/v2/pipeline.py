"""The assistant under test: a ReAct loop (model <-> tools) over an AgentDojo environment.

Tool calls that change state run only if the user's request asks for them (see action_gate.py).
"""
from pathlib import Path

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline
from agentdojo.agent_pipeline.basic_elements import InitQuery, SystemMessage
from agentdojo.agent_pipeline.tool_execution import ToolsExecutionLoop, tool_result_to_str

from agent.variants.v2.action_gate import ActionGate, ActionPlan
from agent.llm import StrictLocalLLM

SYSTEM_PROMPT = Path(__file__).parents[2] / "system_prompt.md"


def build_pipeline(base_url: str, model: str, context_tokens: int,
                   system_prompt: str | None = None) -> tuple[AgentPipeline, StrictLocalLLM]:
    llm = StrictLocalLLM(base_url, model, context_tokens)
    pipeline = AgentPipeline([
        SystemMessage(system_prompt or SYSTEM_PROMPT.read_text()),
        InitQuery(),
        ActionPlan(llm),
        llm,
        ToolsExecutionLoop([ActionGate(llm, tool_result_to_str), llm]),
    ])
    pipeline.name = "local"
    return pipeline, llm
