"""The assistant under test: a ReAct loop (model <-> tools) over an AgentDojo environment."""
from pathlib import Path

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline
from agentdojo.agent_pipeline.basic_elements import InitQuery, SystemMessage
from agentdojo.agent_pipeline.tool_execution import ToolsExecutionLoop, ToolsExecutor, tool_result_to_str

from agent.llm import StrictLocalLLM

SYSTEM_PROMPT = Path(__file__).with_name("system_prompt.md")


def build_pipeline(base_url: str, model: str, context_tokens: int,
                   system_prompt: str | None = None) -> tuple[AgentPipeline, StrictLocalLLM]:
    llm = StrictLocalLLM(base_url, model, context_tokens)
    pipeline = AgentPipeline([
        SystemMessage(system_prompt or SYSTEM_PROMPT.read_text()),
        InitQuery(),
        llm,
        ToolsExecutionLoop([ToolsExecutor(tool_result_to_str), llm]),
    ])
    pipeline.name = "local"
    return pipeline, llm
