"""What a prompt asks the fake model to do: the tool calls it names, in order, and how the reply is chosen.

Read from the prompt's shape, never executed: a shell command is named between backticks after ``run``, a file to read
or a skill to load between backticks after its tool, a subagent's instructions follow ``whose instructions are:``, and
the reply is the word the prompt asks for."""

from __future__ import annotations

import re
from dataclasses import dataclass

_RUN = re.compile(r"\brun `([^`]+)`")
_READ = re.compile(r"read tool exactly once to read `([^`]+)`")
_SKILL = re.compile(r"skill tool exactly once to load the skill named `([^`]+)`")
_INSTRUCTIONS = re.compile(
    r"whose instructions are: (?P<body>.+?)(?:\. (?:Then report|Wait for|Do not wait)|$)", re.DOTALL
)
_REFUSED_REPLY = re.compile(r"[Ii]f (?:it|that) is refused[^;.]*;\s*reply ([A-Z]+)")
_REFUSED_CLAUSE = re.compile(r"[Ii]f (?:it|that) is refused[^.]*\.")
_WORD = re.compile(r"reply with the word ([A-Z]+)", re.IGNORECASE)
_LINE = re.compile(r"reply with the ([A-Z]+) line only", re.IGNORECASE)
_BARE = re.compile(r"\b[Rr]eply ([A-Z]{2,})\b")
_CHOICE = "<Choice>pass</Choice>"
_REPORTS = ("report both outputs", "report its exact output", "reply with its output", "report its output")

CHOICE_REPLY = "<Choice>pass</Choice> The command ran and its result holds."
DEFAULT_REPLY = "OK"


@dataclass(frozen=True)
class Shell:
    command: str


@dataclass(frozen=True)
class Read:
    path: str


@dataclass(frozen=True)
class Skill:
    name: str


@dataclass(frozen=True)
class Subagent:
    instructions: str
    background: bool


type ToolStep = Shell | Read | Skill | Subagent


@dataclass(frozen=True)
class Plan:
    """``prompt`` is the prompt's own text, a subagent's instructions cut out, which is what the reply is read from."""

    prompt: str
    steps: tuple[ToolStep, ...]

    def reply(self, outputs: list[str], *, refused: bool) -> str:
        """The final assistant text, given each tool call's output and whether any was refused or withheld."""
        prompt = self.prompt
        if refused and (match := _REFUSED_REPLY.search(prompt)):
            return match.group(1)
        body = _REFUSED_CLAUSE.sub("", prompt)
        if _CHOICE in body:
            return CHOICE_REPLY
        if match := _WORD.search(body):
            return match.group(1).upper()
        if match := _LINE.search(body):
            prefix = match.group(1).upper() + "="
            lines = [line for output in outputs for line in output.splitlines() if line.startswith(prefix)]
            return lines[0] if lines else ""
        if any(phrase in body.lower() for phrase in _REPORTS):
            return "\n".join(output.strip() for output in outputs if output.strip())
        if "otherwise reply with its first line" in body.lower() and outputs:
            return outputs[0].splitlines()[0] if outputs[0] else ""
        words = _BARE.findall(body)
        return words[-1] if words else DEFAULT_REPLY


def plan(prompt: str) -> Plan:
    """The tool calls ``prompt`` names, in order: shell, read and skill calls before a subagent, then the subagent."""
    instructions = _INSTRUCTIONS.search(prompt) if "subagent tool" in prompt else None
    head = prompt[: instructions.start()] if instructions else prompt
    steps: list[ToolStep] = []
    found = [*_RUN.finditer(head), *_READ.finditer(head), *_SKILL.finditer(head)]
    for match in sorted(found, key=lambda each: each.start()):
        if match.re is _READ:
            steps.append(Read(match.group(1)))
        elif match.re is _SKILL:
            steps.append(Skill(match.group(1)))
        else:
            steps.append(Shell(match.group(1)))
    own = prompt
    if instructions:
        body = instructions.group("body").strip()
        steps.append(Subagent(body[0].upper() + body[1:], background="background set to true" in prompt))
        own = prompt[: instructions.start("body")] + prompt[instructions.end("body") :]
    return Plan(own, tuple(steps))


__all__ = ["CHOICE_REPLY", "DEFAULT_REPLY", "Plan", "Read", "Shell", "Skill", "Subagent", "ToolStep", "plan"]
