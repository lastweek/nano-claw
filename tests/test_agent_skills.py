"""Tests for agent integration with skills."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

from src.agent import Agent
from src.config import Config, config
from src.context import CompactedContextSummary, Context
from src.skills import SkillManager
from src.tools.skill import LoadSkillTool
from src.tools import ToolRegistry


def write_skill(
    skill_dir: Path,
    *,
    name: str = "pdf",
    description: str = "Handle PDFs well",
    short_description: str = "PDF workflows",
    body: str = "Prefer visual PDF checks.",
) -> Path:
    """Create a minimal skill bundle."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "metadata:\n"
        f"  short-description: {short_description}\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return skill_file


class StubLLM:
    """Stub LLM that records messages and returns scripted responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.logger = None

    provider = "stub"
    model = "stub-model"
    base_url = None

    def chat(self, messages, tools=None, log_context=None):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})
        return self.responses.pop(0), SimpleNamespace(iteration=None)


class DummyTool:
    """Small schema-only tool stub for prompt tests."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    def to_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def test_system_prompt_includes_repo_and_user_catalog_skills(temp_dir):
    """Repo-local and user-global skills should appear in the system prompt catalog."""
    repo_root = temp_dir / "repo"
    user_root = temp_dir / "user-skills"
    write_skill(repo_root / ".nano-claw" / "skills" / "pdf")
    write_skill(
        user_root / "terraform",
        name="terraform",
        description="Handle Terraform",
        short_description="Terraform workflows",
        body="Use terraform plan first.",
    )
    manager = SkillManager(repo_root=repo_root, user_root=user_root)
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    llm = StubLLM([{"role": "assistant", "content": "Done"}])
    context = Context.create(cwd=str(repo_root))
    agent = Agent(llm, tools, context, skill_manager=manager)

    agent.run("help")

    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "Available skills in this session:" in system_prompt
    assert "- pdf: PDF workflows" in system_prompt
    assert "- terraform: Terraform workflows" in system_prompt
    assert "Prefer visual PDF checks." not in system_prompt
    assert "Use terraform plan first." not in system_prompt


def test_system_prompt_includes_missing_capability_guidance_when_tools_exist(temp_dir):
    """Capability-request guidance should appear only when the capability tools are available."""
    repo_root = temp_dir / "repo"
    write_skill(repo_root / ".nano-claw" / "skills" / "pdf")
    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    tools.register(DummyTool("find_capabilities", "Search missing capabilities."))
    tools.register(DummyTool("request_capability", "Record a missing capability request."))
    llm = StubLLM([{"role": "assistant", "content": "Done"}])
    context = Context.create(cwd=str(repo_root))
    agent = Agent(llm, tools, context, skill_manager=manager)

    agent.run("help")

    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "Missing capability guidance:" in system_prompt
    assert "Do not invent tool names." in system_prompt
    assert "find_capabilities" in system_prompt
    assert "request_capability" in system_prompt


def test_pinned_skills_are_preloaded_outside_system_prompt(temp_dir):
    """Pinned skills should be injected as synthetic preload messages, not system text."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".nano-claw" / "skills" / "pdf",
        body="Always inspect layout before edits.",
    )
    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    llm = StubLLM([{"role": "assistant", "content": "Done"}])
    context = Context.create(cwd=str(repo_root))
    context.activate_skill("pdf")
    agent = Agent(llm, tools, context, skill_manager=manager)

    agent.run("help")

    messages = llm.calls[0]["messages"]
    system_prompt = messages[0]["content"]
    assert "Always inspect layout before edits." not in system_prompt

    preload_assistant = next(
        message
        for message in messages
        if message["role"] == "assistant" and message.get("tool_calls")
    )
    assert preload_assistant["tool_calls"][0]["function"]["name"] == "load_skill"

    preload_tool = next(message for message in messages if message["role"] == "tool")
    assert "Always inspect layout before edits." in preload_tool["content"]
    assert messages[-1] == {"role": "user", "content": "help"}
    assert context.messages == [
        {"role": "user", "content": "help"},
        {"role": "assistant", "content": "Done"},
    ]


def test_summary_message_is_injected_before_pinned_skill_preloads(temp_dir):
    """Rolling summaries should be injected before raw history and pinned skill preloads."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".nano-claw" / "skills" / "pdf",
        body="Always inspect layout before edits.",
    )
    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    llm = StubLLM([{"role": "assistant", "content": "Done"}])
    context = Context.create(cwd=str(repo_root))
    context.set_summary(
        CompactedContextSummary(
            updated_at="2026-03-02T00:00:00",
            compaction_count=1,
            covered_turn_count=3,
            covered_message_count=6,
            rendered_text="Conversation summary for earlier turns:\n- Ship compaction",
        )
    )
    context.activate_skill("pdf")
    agent = Agent(llm, tools, context, skill_manager=manager)

    agent.run("help")

    messages = llm.calls[0]["messages"]
    assert messages[1]["role"] == "assistant"
    assert "Conversation summary for earlier turns:" in messages[1]["content"]
    preload_assistant = next(
        message
        for message in messages
        if message["role"] == "assistant" and message.get("tool_calls")
    )
    assert messages.index(preload_assistant) > 1


def test_explicit_skill_mention_preloads_and_cleans_user_message(temp_dir):
    """$skill-name should preload the skill for one turn and strip the token from history."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".nano-claw" / "skills" / "pdf",
        body="Use pypdf when layout is irrelevant.",
    )
    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    llm = StubLLM([{"role": "assistant", "content": "Done"}])
    context = Context.create(cwd=str(repo_root))
    agent = Agent(llm, tools, context, skill_manager=manager)

    agent.run("$pdf summarize this file")

    messages = llm.calls[0]["messages"]
    preload_tool = next(message for message in messages if message["role"] == "tool")
    assert "Use pypdf when layout is irrelevant." in preload_tool["content"]
    assert messages[-1] == {"role": "user", "content": "summarize this file"}
    assert context.messages[0] == {"role": "user", "content": "summarize this file"}


def test_ineligible_skill_mention_does_not_preload(temp_dir):
    """Explicit mentions should not preload skills that fail runtime eligibility checks."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".nano-claw" / "skills" / "macos-finder",
        name="macos-finder",
        description="Finder helper",
        short_description="Finder helper",
        body="Use finder_action.",
    )
    skill_file = repo_root / ".nano-claw" / "skills" / "macos-finder" / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: macos-finder\n"
        "description: Finder helper\n"
        "metadata:\n"
        "  short-description: Finder helper\n"
        "  requires:\n"
        "    os:\n"
        "      - darwin\n"
        "    config:\n"
        "      - macos_tools.enabled\n"
        "      - macos_tools.enable_finder\n"
        "---\n\n"
        "Use finder_action.\n",
        encoding="utf-8",
    )
    runtime_config = Config({"macos_tools": {"enabled": False, "enable_finder": True}})
    manager = SkillManager(
        repo_root=repo_root,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    llm = StubLLM([{"role": "assistant", "content": "Done"}])
    context = Context.create(cwd=str(repo_root))
    agent = Agent(llm, tools, context, skill_manager=manager)

    agent.run("$macos-finder reveal this")

    messages = llm.calls[0]["messages"]
    assert not any(message.get("tool_calls") for message in messages if message["role"] == "assistant")
    assert messages[-1] == {"role": "user", "content": "$macos-finder reveal this"}


def test_load_skill_result_is_ephemeral_and_tool_calls_stay_in_history(temp_dir):
    """Agent-loaded skills should stay in-turn only and preserve assistant tool calls."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".nano-claw" / "skills" / "pdf",
        body="Use pypdf when layout is irrelevant.",
    )
    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    llm = StubLLM(
        [
            {
                "role": "assistant",
                "content": "Loading the PDF skill.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "load_skill",
                        "arguments": json.dumps({"skill_name": "pdf"}),
                    }
                ],
            },
            {"role": "assistant", "content": "Used the skill."},
        ]
    )
    context = Context.create(cwd=str(repo_root))
    agent = Agent(llm, tools, context, skill_manager=manager)

    response = agent.run("help with pdfs")

    assert response == "Used the skill."
    assert len(llm.calls) == 2

    second_call_messages = llm.calls[1]["messages"]
    prior_assistant = next(
        message
        for message in second_call_messages
        if message["role"] == "assistant" and message.get("tool_calls")
    )
    assert prior_assistant["tool_calls"][0]["function"]["name"] == "load_skill"

    tool_messages = [message for message in second_call_messages if message["role"] == "tool"]
    assert tool_messages
    assert "Skill: pdf" in tool_messages[0]["content"]

    assert len(context.messages) == 2
    assert context.messages[0] == {"role": "user", "content": "help with pdfs"}
    assert context.messages[1] == {"role": "assistant", "content": "Used the skill."}
    assert all("Skill: pdf" not in str(message["content"]) for message in context.messages)


def test_skill_event_callback_reports_preloads_and_tool_loads(temp_dir):
    """Skill debug callback should receive explicit preloads and real tool loads."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".nano-claw" / "skills" / "pdf",
        body="Prefer visual PDF checks.",
    )
    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    llm = StubLLM(
        [
            {
                "role": "assistant",
                "content": "Loading the skill.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "load_skill",
                        "arguments": json.dumps({"skill_name": "pdf"}),
                    }
                ],
            },
            {"role": "assistant", "content": "Done"},
        ]
    )
    context = Context.create(cwd=str(repo_root))
    agent = Agent(llm, tools, context, skill_manager=manager)

    events = []
    agent.set_skill_event_callback(lambda event, details: events.append((event, details)))

    response = agent.run("$pdf help with pdfs")

    assert response == "Done"
    assert ("preload", {
        "skill_name": "pdf",
        "reason": "explicit",
        "source": "repo",
        "catalog_visible": True,
        "skill_file": str((repo_root / ".nano-claw" / "skills" / "pdf" / "SKILL.md").resolve()),
    }) in events
    assert ("tool_load_requested", {"skill_name": "pdf", "iteration": 0}) in events
    assert ("tool_load_succeeded", {"skill_name": "pdf", "iteration": 0}) in events


def test_activity_events_report_preloads_and_tool_lifecycle(temp_dir):
    """Agent should emit user-safe activity events for the CLI timeline."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".nano-claw" / "skills" / "pdf",
        body="Prefer visual PDF checks.",
    )
    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    llm = StubLLM(
        [
            {
                "role": "assistant",
                "content": "Loading the skill.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "load_skill",
                        "arguments": json.dumps({"skill_name": "pdf"}),
                    }
                ],
            },
            {"role": "assistant", "content": "Done"},
        ]
    )
    context = Context.create(cwd=str(repo_root))
    agent = Agent(llm, tools, context, skill_manager=manager)

    events = []
    response = agent.run("$pdf help with pdfs", on_event=lambda event: events.append(event))

    assert response == "Done"
    assert [event.kind for event in events] == [
        "skill_preload",
        "llm_call_started",
        "llm_call_finished",
        "skill_load_requested",
        "tool_call_started",
        "tool_call_finished",
        "skill_load_succeeded",
        "llm_call_started",
        "llm_call_finished",
        "turn_completed",
    ]
    assert events[0].details["skill_name"] == "pdf"
    assert events[3].details["skill_name"] == "pdf"
    assert events[5].details["success"] is True
    assert events[-1].details["skills_used"] == ["pdf"]


def test_llm_log_includes_inline_skill_and_tool_timeline(temp_dir, monkeypatch):
    """llm.log should inline skill and tool activity in chronological order."""
    monkeypatch.setattr(config.logging, "log_dir", str(temp_dir / "logs"))

    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".nano-claw" / "skills" / "pdf",
        body="Prefer visual PDF checks.",
    )
    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(manager))
    llm = StubLLM(
        [
            {
                "role": "assistant",
                "content": "Loading the skill.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "load_skill",
                        "arguments": json.dumps({"skill_name": "pdf"}),
                    }
                ],
            },
            {"role": "assistant", "content": "Done"},
        ]
    )
    context = Context.create(cwd=str(repo_root))
    agent = Agent(llm, tools, context, skill_manager=manager)

    response = agent.run("$pdf help with pdfs")
    assert response == "Done"
    agent.logger.close()

    session_dir = agent.logger.session_dir
    assert session_dir is not None
    llm_log = (session_dir / "llm.log").read_text()
    skill_idx = llm_log.index("SKILL EVENT")
    tool_call_idx = llm_log.index("TOOL CALL")
    tool_result_idx = llm_log.index("TOOL RESULT")
    turn_end_idx = llm_log.index("TURN END")

    assert "\"skill_name\": \"pdf\"" in llm_log
    assert "\"reason\": \"explicit\"" in llm_log
    assert "\"tool_name\": \"load_skill\"" not in llm_log
    assert "\"skill_name\": \"pdf\"" in llm_log
    assert "\"output\": \"Skill: pdf" in llm_log
    assert skill_idx < tool_call_idx < tool_result_idx < turn_end_idx
