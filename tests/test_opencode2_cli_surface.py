"""Coverage for the OpenCode 2 CLI-surface mode (`mock-opencode-2 emit`): the emit contract, the artifact running
stdlib-only under a bare system `python3`, the managed service it registers and answers as, each lever's observable
change, and the prompt plans against the proof's own prompts in the sibling `blizzard`."""

from __future__ import annotations

import ast
import base64
import importlib.util
import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest

from blizzard_mock.harness.opencode2_surface import _baked
from blizzard_mock.harness.opencode2_surface import emit as surface_emit
from blizzard_mock.harness.opencode2_surface import levers as surface_levers
from blizzard_mock.harness.opencode2_surface.domain import rules, script, shell

_PACKAGE = Path(surface_emit.__file__).resolve().parent
_BLIZZARD = Path(os.environ.get("BLIZZARD_SOURCE") or Path(__file__).resolve().parents[2] / "blizzard")
_PROOF_SCRIPT = _BLIZZARD / "src" / "blizzard" / "runner" / "harness" / "opencode_v2" / "compatibility" / "script.py"

type Json = dict[str, object]


def _emit_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "blizzard_mock.harness.facades.opencode2", "emit", *args],
        capture_output=True,
        text=True,
    )


def _artifact(tmp_path: Path, *levers: str, version: str | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "opencode"
    args = ["--out", str(out), *(arg for lever in levers for arg in ("--lever", lever))]
    proc = _emit_cli(*args, *(["--version", version] if version else []))
    assert proc.returncode == 0, proc.stderr
    return out


def _bare_env(home: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }


class _Service:
    """One emitted artifact's service under an isolated home, stopped on teardown."""

    def __init__(self, binary: Path, home: Path) -> None:
        self.binary = binary
        self.env = _bare_env(home)
        self.registration_path = home / ".local" / "state" / "opencode" / "service.json"

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.binary), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def registration(self) -> Json:
        return json.loads(self.registration_path.read_text())

    def call(self, method: str, path: str, body: object | None = None, *, auth: bool = True) -> tuple[int, object]:
        record = self.registration()
        request = urllib.request.Request(
            f"{record['url']}{path}", data=json.dumps(body).encode() if body is not None else None, method=method
        )
        if auth:
            token = base64.b64encode(f"opencode:{record['password']}".encode()).decode()
            request.add_header("authorization", f"Basic {token}")
        request.add_header("content-type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def data(self, method: str, path: str, body: object | None = None) -> object:
        status, answer = self.call(method, path, body)
        assert 200 <= status < 300, (method, path, status)
        assert isinstance(answer, dict)
        return answer["data"]

    def open_session(self, session_id: str, directory: Path, rules: list[Json] | None = None) -> None:
        self.data(
            "POST",
            "/api/session",
            {"id": session_id, "location": {"directory": str(directory)}, "metadata": {}, "permissions": rules or []},
        )

    def settle(self, session_id: str, timeout: float = 30) -> list[Json]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            active = self.data("GET", "/api/session/active")
            assert isinstance(active, dict)
            if session_id not in active:
                messages = self.data("GET", f"/api/session/{session_id}/message")
                assert isinstance(messages, list)
                return messages
            time.sleep(0.2)
        raise AssertionError(f"{session_id} did not settle")


class _Starter(Protocol):
    def __call__(self, binary: Path, home: Path | None = None) -> _Service: ...


@pytest.fixture
def start_service(tmp_path: Path) -> Iterator[_Starter]:
    started: list[_Service] = []

    def start(binary: Path, home: Path | None = None) -> _Service:
        service = _Service(binary, home or tmp_path / "home")
        started.append(service)
        return service

    yield start
    for service in started:
        service.cli("service", "stop")


def _objects(value: object) -> list[Json]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _tool_parts(messages: list[Json]) -> list[Json]:
    return [part for message in messages for part in _objects(message.get("content")) if part.get("type") == "tool"]


def _output(part: Json) -> str:
    state = part["state"]
    assert isinstance(state, dict)
    content = state.get("content") or []
    assert isinstance(content, list)
    return "".join(str(item["text"]) for item in content if isinstance(item, dict))


# --------------------------------------------------------------------------- #
# `mock-opencode-2 emit`'s contract
# --------------------------------------------------------------------------- #


def test_emit_rejects_every_unknown_lever_and_writes_no_file(tmp_path: Path) -> None:
    out = tmp_path / "opencode"
    proc = _emit_cli("--out", str(out), "--lever", "NOT_A_LEVER", "--lever", "ALSO_NOT", "--lever", "NO_PRICES")
    assert proc.returncode == 2
    assert "ALSO_NOT, NOT_A_LEVER" in proc.stderr
    assert not out.exists()


def test_the_artifact_reports_its_baked_version_under_a_bare_system_python3(tmp_path: Path) -> None:
    for version, home in (("2.1.4", tmp_path / "a"), ("2.0.0", tmp_path / "b")):
        home.mkdir()
        out = _artifact(home, version=None if version == "2.1.4" else version)
        assert out.stat().st_mode & stat.S_IXUSR
        proc = subprocess.run([str(out), "--version"], env=_bare_env(home), capture_output=True, text=True)
        assert (proc.returncode, proc.stdout) == (0, f"opencode v{version}\n")


def test_the_artifact_refuses_a_surface_it_does_not_answer(tmp_path: Path) -> None:
    proc = subprocess.run([str(_artifact(tmp_path)), "run", "hi"], env=_bare_env(tmp_path), capture_output=True)
    assert proc.returncode == 2


def test_baked_module_fields_match_emits_generated_template() -> None:
    def fields(source: str) -> set[str]:
        return {
            node.target.id
            for node in ast.parse(source).body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }

    rendered = surface_emit._render_baked(frozenset({surface_levers.Lever.NO_PRICES}), "2.1.4")
    assert fields(Path(_baked.__file__).read_text()) == fields(rendered)


def test_every_artifact_module_imports_only_the_standard_library() -> None:
    for path in _PACKAGE.rglob("*.py"):
        if path.name == "emit.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in sys.stdlib_module_names, f"{path.name} imports {name}"


def test_the_catalog_names_every_lever_once() -> None:
    assert set(surface_levers.CATALOG) == {lever.value for lever in surface_levers.Lever}


# --------------------------------------------------------------------------- #
# The managed service
# --------------------------------------------------------------------------- #


def test_service_start_registers_an_authenticated_loopback_service_and_stop_ends_it(
    tmp_path: Path, start_service: _Starter
) -> None:
    service = start_service(_artifact(tmp_path))
    assert service.cli("service", "start").returncode == 0
    record = service.registration()
    assert set(record) == {"id", "version", "url", "pid", "password"}
    assert str(record["url"]).startswith("http://127.0.0.1:")
    assert stat.S_IMODE(service.registration_path.stat().st_mode) == 0o600
    assert service.call("GET", "/api/info", auth=False)[0] == 401
    status, info = service.call("GET", "/api/info")
    assert status == 200 and isinstance(info, dict)
    assert (info["version"], info["pid"]) == ("2.1.4", record["pid"])
    assert "already running" in service.cli("service", "start").stdout
    assert service.registration()["id"] == record["id"]
    assert service.cli("service", "status").returncode == 0
    assert service.cli("service", "stop").returncode == 0
    assert not service.registration_path.exists()
    assert service.cli("service", "status").returncode == 1


def test_a_turn_runs_the_fixed_shell_against_the_session_environment_and_spawns_a_child(
    tmp_path: Path, start_service: _Starter
) -> None:
    service = start_service(_artifact(tmp_path))
    service.cli("service", "start")
    service.open_session("ses_root", tmp_path)
    assert service.call("PUT", "/api/session/ses_root/environment", {"variables": {"BLZ_PROOF_ENV": "x1"}})[0] == 204
    prompt = (
        "Use the shell tool exactly once to run `echo ROOT=${BLZ_PROOF_ENV:-}`. Then use the subagent tool exactly "
        "once to launch a `general` subagent whose instructions are: use the shell tool exactly once to run "
        "`echo CHILD=${BLZ_PROOF_ENV:-}` and report its exact output. Then report both outputs verbatim and stop."
    )
    service.data("POST", "/api/session/ses_root/prompt", {"text": prompt})
    messages = service.settle("ses_root")
    shell_part, spawn = _tool_parts(messages)
    assert _output(shell_part) == "ROOT=x1\n"
    children = service.data("GET", "/api/session?parentID=ses_root")
    assert isinstance(children, list) and len(children) == 1
    child = children[0]
    assert isinstance(child, dict) and child["parentID"] == "ses_root"
    assert isinstance(spawn["state"], dict) and spawn["state"]["metadata"]["sessionID"] == child["id"]
    child_parts = _tool_parts(service.settle(str(child["id"])))
    assert _output(child_parts[0]) == "CHILD=\n"
    assert messages[-1]["type"] == "idle" and messages[-1]["outcome"] == "succeeded"


def test_a_service_restart_is_a_new_lifetime_that_keeps_the_sessions(tmp_path: Path, start_service: _Starter) -> None:
    service = start_service(_artifact(tmp_path))
    service.cli("service", "start")
    before = service.registration()
    service.open_session("ses_kept", tmp_path)
    assert service.cli("service", "restart").returncode == 0
    after = service.registration()
    assert (after["id"], after["pid"]) != (before["id"], before["pid"])
    assert isinstance(service.data("GET", "/api/session/ses_kept"), dict)


# --------------------------------------------------------------------------- #
# Levers
# --------------------------------------------------------------------------- #


def test_never_registered_starts_nothing(tmp_path: Path, start_service: _Starter) -> None:
    service = start_service(_artifact(tmp_path, "NEVER_REGISTERED"))
    assert service.cli("service", "start").returncode == 0
    assert not service.registration_path.exists()


def test_never_answers_registers_an_address_nothing_answers_at(tmp_path: Path, start_service: _Starter) -> None:
    service = start_service(_artifact(tmp_path, "NEVER_ANSWERS"))
    assert service.cli("service", "start").returncode == 0
    with pytest.raises(urllib.error.URLError):
        service.call("GET", "/api/info")


def test_version_mismatch_reports_the_next_patch_release(tmp_path: Path, start_service: _Starter) -> None:
    service = start_service(_artifact(tmp_path, "VERSION_MISMATCH"))
    service.cli("service", "start")
    _, info = service.call("GET", "/api/info")
    assert isinstance(info, dict) and info["version"] == "2.1.5"
    assert service.cli("--version").stdout == "opencode v2.1.4\n"


def test_no_prices_empties_every_listed_price(tmp_path: Path, start_service: _Starter) -> None:
    priced = start_service(_artifact(tmp_path / "priced"), tmp_path / "priced-home")
    bare = start_service(_artifact(tmp_path, "NO_PRICES"))
    for service in (priced, bare):
        service.cli("service", "start")
    listed = {service: service.data("GET", "/api/model") for service in (priced, bare)}
    assert all(m["cost"] for m in _objects(listed[priced]))
    assert all(m["cost"] == [] for m in _objects(listed[bare]))


@pytest.mark.parametrize(
    ("lever", "observed"),
    [
        ("DISTORTED_SHAPES", lambda data: isinstance(data, dict)),
        (
            "UNKNOWN_PART_TYPES",
            lambda data: "tool-call" in json.dumps(data) and '"type": "tool"' not in json.dumps(data),
        ),
    ],
)
def test_shape_levers_distort_the_message_list(
    tmp_path: Path, start_service: _Starter, lever: str, observed: object
) -> None:
    service = start_service(_artifact(tmp_path, lever))
    service.cli("service", "start")
    service.open_session("ses_shape", tmp_path)
    service.data("POST", "/api/session/ses_shape/prompt", {"text": "Use the shell tool exactly once to run `echo A`."})
    deadline = time.monotonic() + 15
    while "ses_shape" in str(service.data("GET", "/api/session/active")) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert callable(observed) and observed(service.data("GET", "/api/session/ses_shape/message"))


def test_events_dropped_closes_the_stream_at_once(tmp_path: Path, start_service: _Starter) -> None:
    service = start_service(_artifact(tmp_path, "EVENTS_DROPPED"))
    service.cli("service", "start")
    record = service.registration()
    request = urllib.request.Request(f"{record['url']}/api/event")
    token = base64.b64encode(f"opencode:{record['password']}".encode()).decode()
    request.add_header("authorization", f"Basic {token}")
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
        assert response.read() == b""


def test_interrupt_ignored_answers_interrupted_and_the_turn_runs_on(tmp_path: Path, start_service: _Starter) -> None:
    service = start_service(_artifact(tmp_path, "INTERRUPT_IGNORED"))
    service.cli("service", "start")
    service.open_session("ses_busy", tmp_path)
    service.data("POST", "/api/session/ses_busy/prompt", {"text": "Use the shell tool exactly once to run `sleep 3`."})
    _, answer = service.call("POST", "/api/session/ses_busy/interrupt")
    assert answer == {"interrupted": True}
    messages = service.settle("ses_busy")
    assert messages[-1]["outcome"] == "succeeded"


def test_restarts_mid_turn_replaces_the_service_once_while_its_first_shell_tool_runs(
    tmp_path: Path, start_service: _Starter
) -> None:
    service = start_service(_artifact(tmp_path, "RESTARTS_MID_TURN"))
    service.cli("service", "start")
    first = service.registration()
    service.open_session("ses_restart", tmp_path)
    service.data(
        "POST", "/api/session/ses_restart/prompt", {"text": "Use the shell tool exactly once to run `echo A`."}
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = json.loads(service.registration_path.read_text()) if service.registration_path.exists() else first
        if current["id"] != first["id"]:
            break
        time.sleep(0.2)
    second = service.registration()
    assert second["id"] != first["id"]
    service.settle("ses_restart")
    service.data(
        "POST", "/api/session/ses_restart/prompt", {"text": "Use the shell tool exactly once to run `echo B`."}
    )
    service.settle("ses_restart")
    assert service.registration()["id"] == second["id"]


# --------------------------------------------------------------------------- #
# Domain: prompt plans, rules, the fake shell
# --------------------------------------------------------------------------- #


def test_a_plan_reads_shell_read_and_subagent_calls_and_the_reply_the_prompt_asks_for() -> None:
    plan = script.plan(
        "Use the subagent tool exactly once, with background set to true, to launch a `general` subagent whose "
        "instructions are: use the shell tool exactly once to run `sleep 8; echo DONE-X` and report its output. Do "
        "not wait for it; reply STARTED immediately."
    )
    assert plan.steps == (
        script.Subagent("Use the shell tool exactly once to run `sleep 8; echo DONE-X` and report its output", True),
    )
    assert plan.reply([], refused=False) == "STARTED"
    deny = script.plan(
        "Use the shell tool exactly once to run `touch x`. If it is refused, do not retry; reply REFUSED."
    )
    assert deny.steps == (script.Shell("touch x"),)
    assert (deny.reply([""], refused=True), deny.reply([""], refused=False)) == ("REFUSED", "OK")
    lane = script.plan("Use the shell tool to run `echo LANE=a`. Then reply with the LANE line only.")
    assert lane.reply(["LANE=a\n"], refused=False) == "LANE=a"
    assert (
        script.plan("Then reply with exactly `<Choice>pass</Choice>`.").reply([], refused=False) == script.CHOICE_REPLY
    )


def test_the_last_matching_rule_wins_and_a_wholly_denied_tool_is_withheld() -> None:
    ruleset: list[rules.Rule] = [
        {"action": "shell", "resource": "touch *", "effect": "ask"},
        {"action": "shell", "resource": "touch secret", "effect": "deny"},
        {"action": "read", "resource": "*", "effect": "deny"},
    ]
    assert rules.evaluate(ruleset, "shell", "touch a") == "ask"
    assert rules.evaluate(ruleset, "shell", "touch secret") == "deny"
    assert rules.evaluate(ruleset, "shell", "echo") == "allow"
    assert rules.withheld(ruleset, "read") and not rules.withheld(ruleset, "shell")


def test_the_fake_shell_expands_from_the_session_environment_and_refuses_anything_else() -> None:
    parsed = shell.parse("echo $$ > pid; sleep 2; echo A=${X:-} B=${Y:-d} $X", {"X": "1"}, 42)
    assert parsed == (shell.Echo("42", "pid"), shell.Sleep(2.0), shell.Echo("A=1 B=d 1"))
    with pytest.raises(shell.UnsupportedCommandError):
        shell.parse("rm -rf /", {}, 1)


@pytest.mark.needs_blizzard
def test_every_proof_prompt_plans_to_the_tool_calls_it_names() -> None:
    """The proof's prompts live in `blizzard`; each must plan here to the calls and reply it asks for."""
    assert _PROOF_SCRIPT.is_file(), f"no sibling blizzard at {_BLIZZARD}; set $BLIZZARD_SOURCE"
    spec = importlib.util.spec_from_file_location("_proof_script", _PROOF_SCRIPT)
    assert spec is not None and spec.loader is not None
    proof = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proof)
    sleep = proof.SLEEP_COMMAND
    expected: dict[str, tuple[tuple[script.ToolStep, ...], str]] = {
        "VERBATIM_PROMPT": ((), "READY"),
        "SLEEP_PROMPT": ((script.Shell(sleep),), "DONE"),
        "JUDGEMENT_PROMPT": ((script.Shell("echo tool-output-not-the-verdict"),), script.CHOICE_REPLY),
        "QUEUE_PROMPT": ((), "QUEUED"),
        "STEER_PROMPT": ((), "STEERED"),
        "DENY_PROMPT": ((script.Shell(proof.DENY_SHELL_COMMAND),), "OK"),
        "DENY_TOOL_PROMPT": ((script.Read("README.md"),), ""),
        "ASK_PROMPT": ((script.Shell(proof.ASK_COMMAND),), "ASKED"),
        "COMPACT_SEED_PROMPT": ((), "SEED"),
        "RESUME_PROMPT": ((), "RESUMED"),
        "TAKEOVER_PROMPT": ((), "TAKEN"),
        "RESTART_PROMPT": ((script.Shell(proof.RESTART_COMMAND),), "DONE"),
    }
    for name, (steps, reply) in expected.items():
        plan = script.plan(getattr(proof, name))
        assert plan.steps == steps, name
        assert plan.reply([""], refused=False) == reply, name
    env_plan = script.plan(proof.ENV_PROMPT)
    assert [type(step) for step in env_plan.steps] == [script.Shell, script.Subagent]
    child_sleep = script.plan(proof.CHILD_SLEEP_PROMPT).steps
    assert isinstance(child_sleep[0], script.Subagent) and script.plan(child_sleep[0].instructions).steps == (
        script.Shell(sleep),
    )
    background = script.plan(proof.BACKGROUND_CHILD_PROMPT).steps
    assert isinstance(background[0], script.Subagent) and background[0].background
    concurrent = script.plan(proof.CONCURRENT_PROMPT)
    assert [step.command for step in concurrent.steps if isinstance(step, script.Shell)] == [
        "echo LANE=${BLZ_PROOF_ENV:-}",
        "touch proof-lane-$BLZ_PROOF_ENV",
    ]
    assert script.plan(proof.DENY_PROMPT).reply([""], refused=True) == "REFUSED"
