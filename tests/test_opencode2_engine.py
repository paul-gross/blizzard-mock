"""Coverage for OpenCode 2's engine-backed mock service (`mock-opencode-2 emit-engine`): a turn's prompt runs as a
script in a child process of its own, under the fence, writing the session's messages through the service; the
session's rules judge every tool call it makes; children run scripts of their own; an interrupt ends the child; and
each turn-level helper ends its turn with the corpus's error shape."""

from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from blizzard_mock.harness import engine as exec_engine
from blizzard_mock.harness.opencode2_core import emit as core_emit
from blizzard_mock.harness.opencode2_engine import emit as engine_emit
from blizzard_mock.harness.opencode2_engine.domain import failures, fence
from tests.opencode2_service import Json, Service, Starter, artifact, emit_cli, is_json, objects, output, tool_parts

_PACKAGE = Path(engine_emit.__file__).resolve().parent
_CORE = Path(core_emit.__file__).resolve().parent
_FENCE = {fence.FENCE_ENV_VAR: fence.FENCE_ENV_VALUE}


def _engine(tmp_path: Path, *levers: str) -> Path:
    return artifact(tmp_path / "bin", *levers, verb="emit-engine")


def _repo(tmp_path: Path, *, fenced: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    if fenced:
        (repo / fence.FENCE_MARKER_FILENAME).touch()
    return repo


def _started(start_service: Starter, tmp_path: Path, *levers: str, fenced: bool = True) -> Service:
    service = start_service(_engine(tmp_path, *levers), extra_env=_FENCE if fenced else None)
    assert service.cli("service", "start").returncode == 0
    return service


def _turn(service: Service, session_id: str, script: str, *, timeout: float = 30) -> list[Json]:
    service.data("POST", f"/api/session/{session_id}/prompt", {"text": script})
    return service.settle(session_id, timeout)


def _idle(messages: list[Json]) -> Json:
    idles = [message for message in messages if message.get("type") == "idle"]
    assert idles, messages
    return idles[-1]


def _assistants(messages: list[Json]) -> list[Json]:
    return [message for message in messages if message.get("type") == "assistant"]


def _texts(message: Json) -> list[str]:
    return [str(part["text"]) for part in objects(message.get("content")) if part.get("type") == "text"]


def _wait_for(predicate: object, timeout: float = 20) -> None:
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("condition not reached")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# -- `mock-opencode-2 emit-engine`'s contract --


def test_emit_engine_rejects_every_unknown_lever_and_writes_no_file(tmp_path: Path) -> None:
    out = tmp_path / "opencode"
    proc = emit_cli("--out", str(out), "--lever", "NOPE", "--lever", "NO_PRICES", verb="emit-engine")
    assert proc.returncode == 2
    assert "NOPE" in proc.stderr
    assert not out.exists()


def test_the_engine_artifact_ships_the_core_and_its_own_package_and_reports_its_version(tmp_path: Path) -> None:
    binary = _engine(tmp_path)
    with zipfile.ZipFile(binary) as zipped:
        packages = {name.split("/")[1] for name in zipped.namelist() if name.count("/") >= 2}
    assert packages == {_CORE.name, _PACKAGE.name}
    proc = subprocess.run(
        ["/usr/bin/python3", str(binary), "--version"], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}
    )
    assert proc.stdout.strip() == "opencode v2.1.4"


def test_every_engine_module_imports_only_the_standard_library_and_its_two_packages() -> None:
    for path in _PACKAGE.rglob("*.py"):
        if path.name == "emit.py":
            continue
        depth = len(path.relative_to(_PACKAGE).parts)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            elif isinstance(node, ast.ImportFrom) and node.level > depth:
                assert node.level == depth + 1 and (node.module or "").split(".")[0] == _CORE.name, path
                continue
            else:
                continue
            for name in names:
                assert name.split(".")[0] in sys.stdlib_module_names, f"{path.name} imports {name}"


def test_the_fence_matches_the_exec_engines() -> None:
    assert (fence.FENCE_ENV_VAR, fence.FENCE_ENV_VALUE, fence.FENCE_MARKER_FILENAME) == (
        exec_engine.FENCE_ENV_VAR,
        exec_engine.FENCE_ENV_VALUE,
        exec_engine.FENCE_MARKER_FILENAME,
    )


# -- The fence --


@pytest.mark.parametrize(("fenced_env", "marker"), [(False, True), (True, False)])
def test_a_turn_outside_the_fence_fails_without_running_its_script(
    tmp_path: Path, start_service: Starter, fenced_env: bool, marker: bool
) -> None:
    service = _started(start_service, tmp_path, fenced=fenced_env)
    repo = _repo(tmp_path, fenced=marker)
    service.open_session("ses_fence", repo)
    idle = _idle(_turn(service, "ses_fence", "open('ran', 'w').close()"))
    assert idle["outcome"] == "failed"
    error = idle["error"]
    assert is_json(error) and "refused to run a script" in str(error["message"])
    assert not (repo / "ran").exists()


# -- A turn's script --


def test_a_script_writes_steps_with_reasoning_text_and_its_scripted_usage(
    tmp_path: Path, start_service: Starter
) -> None:
    service = _started(start_service, tmp_path)
    service.open_session("ses_steps", _repo(tmp_path))
    script = (
        "reasoning('weighing it')\n"
        "usage(input=120, output=9, reasoning=33, cache_read=64, cache_write=2, cost=0.25)\n"
        "shell('true')\n"
        "step()\n"
        "usage(input=7, output=3, cost=0.5)\n"
        "text('DONE')\n"
    )
    messages = _turn(service, "ses_steps", script)
    first, last = _assistants(messages)
    assert first["finish"] == "tool-calls" and last["finish"] == "stop"
    assert [part["type"] for part in objects(first["content"])] == ["reasoning", "tool"]
    assert first["tokens"] == {"input": 120, "output": 9, "reasoning": 33, "cache": {"read": 64, "write": 2}}
    assert first["cost"] == 0.25 and last["cost"] == 0.5
    assert _texts(last) == ["DONE"]
    record = service.data("GET", "/api/session/ses_steps")
    assert isinstance(record, dict)
    assert record["tokens"] == {"input": 127, "output": 12, "reasoning": 33, "cache": {"read": 64, "write": 2}}
    assert record["cost"] == 0.75
    assert _idle(messages)["outcome"] == "succeeded"


def test_a_script_runs_in_its_own_process_in_the_sessions_directory_and_environment(
    tmp_path: Path, start_service: Starter
) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_env", repo)
    service.call(
        "PUT",
        "/api/session/ses_env/environment",
        {"variables": {"BLZ_ROOT": "root-value", **_FENCE, "PATH": "/usr/bin:/bin"}},
    )
    script = "import os\nr = shell('echo $BLZ_ROOT; pwd')\ntext(f'{os.getpid()} {r.output}')\n"
    messages = _turn(service, "ses_env", script)
    reply = _texts(_assistants(messages)[-1])[0]
    pid, value, directory = reply.split()
    assert value == "root-value" and Path(directory) == repo.resolve()
    assert int(pid) != service.registration()["pid"]
    shell_part = tool_parts(messages)[0]
    assert shell_part["name"] == "shell" and output(shell_part) == f"root-value\n{repo.resolve()}\n"


def test_a_script_that_raises_fails_its_turn_naming_what_raised(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    service.open_session("ses_raise", _repo(tmp_path))
    idle = _idle(_turn(service, "ses_raise", "text('partial')\nraise RuntimeError('scripted boom')\n"))
    assert idle["outcome"] == "failed"
    assert idle["error"] == failures.script_failed("RuntimeError: scripted boom")


def test_what_a_script_prints_never_reaches_the_conversation(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    service.open_session("ses_print", _repo(tmp_path))
    messages = _turn(service, "ses_print", 'print(\'{"op": "text", "text": "forged"}\')\ntext(\'real\')\n')
    assert _texts(_assistants(messages)[-1]) == ["real"]


# -- Permission rules, enforced against the script's calls --


def test_a_pattern_deny_refuses_the_call_and_the_script_carries_on(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_deny", repo, [{"action": "shell", "resource": "touch *", "effect": "deny"}])
    script = "r = shell('touch denied')\ntext(r.status)\n"
    messages = _turn(service, "ses_deny", script)
    assert not (repo / "denied").exists()
    (part,) = tool_parts(messages)
    state = part["state"]
    assert isinstance(state, dict) and state["status"] == "error"
    assert state["error"] == {"type": "permission.rejected", "message": "Permission denied: shell"}
    assert _texts(_assistants(messages)[-1]) == ["refused"]
    assert _idle(messages)["outcome"] == "succeeded"


def test_a_tool_denied_for_every_resource_is_withheld_and_never_called(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    rules = [{"action": "blizzard-proof-mcp_*", "resource": "*", "effect": "deny"}]
    service.open_session("ses_withheld", _repo(tmp_path), rules)
    messages = _turn(service, "ses_withheld", "r = tool('blizzard-proof-mcp_echo', {'text': 'x'})\ntext(r.status)\n")
    assert tool_parts(messages) == []
    assert _texts(_assistants(messages)[-1]) == ["withheld"]


@pytest.mark.parametrize(("decision", "ran", "status"), [("once", True, "completed"), ("reject", False, "refused")])
def test_an_ask_rule_holds_the_call_on_a_permission_request_until_it_is_answered(
    tmp_path: Path, start_service: Starter, decision: str, ran: bool, status: str
) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_ask", repo, [{"action": "shell", "resource": "touch *", "effect": "ask"}])
    service.data("POST", "/api/session/ses_ask/prompt", {"text": "r = shell('touch asked')\ntext(r.status)\n"})
    requests: list[Json] = []

    def raised() -> bool:
        requests[:] = objects(service.data("GET", "/api/session/ses_ask/permission"))
        return bool(requests)

    _wait_for(raised)
    (request,) = requests
    assert request["action"] == "shell" and request["resources"] == ["touch asked"] and request["save"] == ["touch *"]
    service.call("POST", f"/api/session/ses_ask/permission/{request['id']}/reply", {"decision": decision})
    messages = service.settle("ses_ask")
    assert (repo / "asked").exists() is ran
    assert _texts(_assistants(messages)[-1]) == [status]


def test_an_always_reply_saves_the_pattern_so_the_next_matching_call_runs_unasked(
    tmp_path: Path, start_service: Starter
) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_always", repo, [{"action": "shell", "resource": "touch *", "effect": "ask"}])
    service.data("POST", "/api/session/ses_always/prompt", {"text": "shell('touch one')\nshell('touch two')\n"})
    requests: list[Json] = []

    def raised() -> bool:
        requests[:] = objects(service.data("GET", "/api/session/ses_always/permission"))
        return bool(requests)

    _wait_for(raised)
    service.call("POST", f"/api/session/ses_always/permission/{requests[0]['id']}/reply", {"decision": "always"})
    service.settle("ses_always")
    assert (repo / "one").exists() and (repo / "two").exists()
    record = service.data("GET", "/api/session/ses_always")
    assert is_json(record)
    assert {"action": "shell", "resource": "touch *", "effect": "allow"} in objects(record["permissions"])


def test_a_permission_request_helper_asks_even_where_the_rules_allow(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    service.open_session("ses_request", _repo(tmp_path))
    service.data("POST", "/api/session/ses_request/prompt", {"text": "text(permission_request('edit', 'a.py').status)"})
    requests: list[Json] = []

    def raised() -> bool:
        requests[:] = objects(service.data("GET", "/api/session/ses_request/permission"))
        return bool(requests)

    _wait_for(raised)
    assert requests[0]["action"] == "edit" and requests[0]["resources"] == ["a.py"]
    service.call("POST", f"/api/session/ses_request/permission/{requests[0]['id']}/reply", {"decision": "once"})
    assert _texts(_assistants(service.settle("ses_request"))[-1]) == ["completed"]


# -- Interrupt, steer, forms --


def test_an_interrupt_ends_the_scripts_process_and_the_command_it_runs(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_stop", repo)
    script = "import os\nopen('script.pid', 'w').write(str(os.getpid()))\nshell('echo $$ > tool.pid; sleep 60')\n"
    service.data("POST", "/api/session/ses_stop/prompt", {"text": script})
    _wait_for(lambda: (repo / "tool.pid").exists() and (repo / "tool.pid").read_text().strip() != "")
    script_pid, tool_pid = int((repo / "script.pid").read_text()), int((repo / "tool.pid").read_text())
    status, answer = service.call("POST", "/api/session/ses_stop/interrupt")
    assert status == 200 and answer == {"interrupted": True}
    messages = service.settle("ses_stop")
    assert _idle(messages)["outcome"] == "interrupted"
    _wait_for(lambda: not _pid_alive(script_pid) and not _pid_alive(tool_pid), 5)
    state = tool_parts(messages)[0]["state"]
    assert (
        isinstance(state, dict)
        and state["status"] == "error"
        and state["error"]
        == {
            "type": "aborted",
            "message": "Tool execution interrupted",
        }
    )


def test_a_steer_ends_the_running_script_at_its_next_call_and_runs_as_the_next(
    tmp_path: Path, start_service: Starter
) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_steer", repo)
    script = (
        "import time\nfor i in range(200):\n    shell(f'echo {i} > tick')\n    time.sleep(0.05)\ntext('UNSTEERED')\n"
    )
    service.data("POST", "/api/session/ses_steer/prompt", {"text": script})
    _wait_for(lambda: (repo / "tick").exists())
    service.data("POST", "/api/session/ses_steer/prompt", {"text": "text('STEERED')", "delivery": "steer"})
    messages = service.settle("ses_steer")
    users = [message["text"] for message in messages if message.get("type") == "user"]
    assert users == [script, "text('STEERED')"]
    assert _texts(_assistants(messages)[-1]) == ["STEERED"]
    assert _idle(messages)["outcome"] == "succeeded"


def test_a_form_holds_the_turn_until_it_is_cancelled(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    service.open_session("ses_form", _repo(tmp_path))
    script = "raised = form('blizzard question', [{'key': 'answer', 'type': 'string'}])\ntext(raised['title'])\n"
    service.data("POST", "/api/session/ses_form/prompt", {"text": script})
    forms: list[Json] = []

    def raised() -> bool:
        forms[:] = objects(service.data("GET", "/api/session/ses_form/form"))
        return bool(forms)

    _wait_for(raised)
    (form,) = forms
    assert form["title"] == "blizzard question" and form["fields"] == [{"key": "answer", "type": "string"}]
    assert service.call("DELETE", f"/api/session/ses_form/form/{form['id']}")[0] == 204
    assert _texts(_assistants(service.settle("ses_form"))[-1]) == ["blizzard question"]


# -- Child sessions --


def test_a_subagent_runs_its_own_script_in_a_child_session_and_process(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    service.open_session("ses_parent", _repo(tmp_path))
    script = (
        "import os\n"
        "child = subagent(\"import os\\ntext(f'CHILD {os.getpid()}')\", description='Report the pid')\n"
        "text(f'{os.getpid()} {child.output} {child.session}')\n"
    )
    messages = _turn(service, "ses_parent", script)
    parent_pid, _, child_pid, child_id = _texts(_assistants(messages)[-1])[0].split()
    assert parent_pid != child_pid
    (spawn,) = tool_parts(messages)
    state = spawn["state"]
    assert isinstance(state, dict) and state["metadata"] == {
        "sessionID": child_id,
        "status": "completed",
        "truncated": False,
    }
    assert isinstance(state["input"], dict) and state["input"]["description"] == "Report the pid"
    children = objects(service.data("GET", "/api/session?parentID=ses_parent"))
    assert [child["id"] for child in children] == [child_id] and children[0]["parentID"] == "ses_parent"


def test_a_background_child_that_outlives_its_root_wakes_it_to_run_the_wake_script(
    tmp_path: Path, start_service: Starter
) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_root", repo)
    child = (
        "import time\\nwhile not __import__('os').path.exists('release'):\\n    time.sleep(0.05)\\ntext('CHILD DONE')"
    )
    script = f"subagent(\"{child}\", background=True, wake=\"text('WOKE ' + wake_result())\")\ntext('ROOT IDLE')\n"
    messages = _turn(service, "ses_root", script)
    assert _texts(_assistants(messages)[-1]) == ["ROOT IDLE"] and _idle(messages)["outcome"] == "succeeded"
    (repo / "release").touch()

    def woke() -> bool:
        return any(
            "WOKE CHILD DONE" in _texts(m)
            for m in _assistants(objects(service.data("GET", "/api/session/ses_root/message")))
        )

    _wait_for(woke)
    messages = service.settle("ses_root")
    synthetic = [message for message in messages if message.get("type") == "synthetic"]
    assert len(synthetic) == 1 and "CHILD DONE" in str(synthetic[0]["text"])


def test_interrupting_a_root_ends_its_running_childs_process(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_tree", repo)
    child = "import os\\nopen('child.pid', 'w').write(str(os.getpid()))\\nhang()"
    service.data("POST", "/api/session/ses_tree/prompt", {"text": f'subagent("{child}")'})
    _wait_for(lambda: (repo / "child.pid").exists() and (repo / "child.pid").read_text() != "")
    child_pid = int((repo / "child.pid").read_text())
    service.call("POST", "/api/session/ses_tree/interrupt")
    assert _idle(service.settle("ses_tree"))["outcome"] == "interrupted"
    _wait_for(lambda: not _pid_alive(child_pid), 5)
    (child_record,) = objects(service.data("GET", "/api/session?parentID=ses_tree"))
    assert child_record["outcome"] == "interrupted"


# -- Turn-level failures --


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("provider_refusal()", failures.provider_refusal()),
        ("usage_limit(60000, kind='provider.rate-limit')", failures.usage_limit(60000, kind="provider.rate-limit")),
        ("usage_limit()", failures.usage_limit()),
        ("overload()", failures.overload()),
        ("unknown_model()", failures.unknown_model({"providerID": "openai", "id": "gpt-6-luna"})),
        (
            "unknown_variant()",
            failures.unknown_variant({"providerID": "openai", "id": "gpt-6-luna", "variant": "high"}),
        ),
    ],
)
def test_each_failure_helper_ends_the_turn_with_its_corpus_error(
    tmp_path: Path, start_service: Starter, call: str, expected: Json
) -> None:
    service = _started(start_service, tmp_path)
    service.open_session("ses_fail", _repo(tmp_path))
    service.call(
        "POST",
        "/api/session/ses_fail/model",
        {"model": {"providerID": "openai", "id": "gpt-6-luna", "variant": "high"}},
    )
    messages = _turn(service, "ses_fail", f"text('before')\n{call}\ntext('never')\n")
    idle = _idle(messages)
    assert idle["outcome"] == "failed" and idle["error"] == expected
    last = _assistants(messages)[-1]
    assert last["error"] == expected and last["finish"] == "error"
    assert _texts(last) == ["before"]


def test_a_model_the_listing_does_not_route_fails_before_the_script_runs(
    tmp_path: Path, start_service: Starter
) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_route", repo)
    service.call("POST", "/api/session/ses_route/model", {"model": {"providerID": "openai", "id": "no-such-model"}})
    idle = _idle(_turn(service, "ses_route", "open('ran', 'w').close()"))
    assert idle["error"] == {"type": "provider.no-route", "message": "Model unavailable: openai/no-such-model"}
    assert not (repo / "ran").exists()


# -- Service-level levers and takeover --


def test_interrupt_ignored_leaves_the_script_running(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path, "INTERRUPT_IGNORED")
    repo = _repo(tmp_path)
    service.open_session("ses_ignored", repo)
    service.data("POST", "/api/session/ses_ignored/prompt", {"text": "shell('touch started; sleep 2')\ntext('RAN ON')"})
    _wait_for(lambda: (repo / "started").exists())
    assert service.call("POST", "/api/session/ses_ignored/interrupt") == (200, {"interrupted": True})
    assert _texts(_assistants(service.settle("ses_ignored"))[-1]) == ["RAN ON"]


def test_restarts_mid_turn_resumes_the_script_in_the_next_lifetime(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path, "RESTARTS_MID_TURN")
    service.open_session("ses_restart", _repo(tmp_path))
    first = service.registration()["id"]
    script = "if resumed():\n    text('RESUMED')\nelse:\n    shell('sleep 20')\n    text('FIRST LIFE')\n"
    service.data("POST", "/api/session/ses_restart/prompt", {"text": script})
    _wait_for(lambda: service.registration_path.exists() and service.registration()["id"] != first, 30)
    messages = service.settle("ses_restart", 40)
    assert _texts(_assistants(messages)[-1]) == ["RESUMED"]
    aborted = _assistants(messages)[0]
    assert aborted["finish"] == "error"


def test_takeover_switches_the_agent_keeps_the_model_and_prompts_through_the_service(
    tmp_path: Path, start_service: Starter
) -> None:
    service = _started(start_service, tmp_path)
    service.open_session("ses_take", _repo(tmp_path))
    luna = {"providerID": "openai", "id": "gpt-6-luna"}
    assert service.call("POST", "/api/session/ses_take/model", {"model": luna})[0] == 204
    tui = subprocess.Popen(
        [str(service.binary), "--session", "ses_take"],
        env=service.env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert tui.stdin is not None
        tui.stdin.write("text('TAKEN OVER')\n")
        tui.stdin.flush()
        _wait_for(
            lambda: any(m.get("type") == "idle" for m in objects(service.data("GET", "/api/session/ses_take/message")))
        )
        messages = service.settle("ses_take")
    finally:
        tui.send_signal(signal.SIGTERM)
        tui.wait(10)
    assert _texts(_assistants(messages)[-1]) == ["TAKEN OVER"]
    record = service.data("GET", "/api/session/ses_take")
    assert isinstance(record, dict) and record["agent"] == "build"
    assert record["model"] == luna
    switches = [(m["type"], m.get("model", m.get("agent"))) for m in messages if str(m["type"]).endswith("-switched")]
    assert switches == [("model-switched", luna), ("agent-switched", "build")]


def test_the_api_subset_pages_messages_lists_the_inbox_compacts_and_reports_itself(
    tmp_path: Path, start_service: Starter
) -> None:
    service = _started(start_service, tmp_path)
    service.data(
        "POST",
        "/api/session",
        {"id": "ses_api", "location": {"directory": str(_repo(tmp_path))}, "metadata": {"blizzard": {"run": "r1"}}},
    )
    _turn(service, "ses_api", "text('one')")
    _turn(service, "ses_api", "text('two')")
    every = objects(service.data("GET", "/api/session/ses_api/message"))
    _, first = service.call("GET", "/api/session/ses_api/message?limit=2")
    assert isinstance(first, dict)
    cursor = first["cursor"]
    assert isinstance(cursor, dict)
    following = str(cursor["next"])
    assert not following.startswith("msg_") and isinstance(cursor["previous"], str)
    _, after = service.call("GET", f"/api/session/ses_api/message?cursor={following}")
    assert isinstance(after, dict)
    after_cursor = after["cursor"]
    assert isinstance(after_cursor, dict)
    assert objects(first["data"]) + objects(after["data"]) == every
    _, tail = service.call("GET", f"/api/session/ses_api/message?cursor={after_cursor['next']}")
    assert isinstance(tail, dict) and tail["data"] == [] and tail["cursor"] == {"previous": None, "next": None}
    refused, body = service.call("GET", f"/api/session/ses_api/message?order=asc&cursor={following}")
    assert refused == 400 and isinstance(body, dict) and body["_tag"] == "InvalidCursorError"
    assert service.call("GET", "/api/session/ses_api/message?limit=201")[0] == 400
    missing, body = service.call("GET", "/api/session/ses_missing/message")
    assert missing == 404 and isinstance(body, dict) and body["_tag"] == "SessionNotFoundError"
    newest = objects(service.data("GET", "/api/session/ses_api/message?order=desc&limit=1"))
    assert newest == every[-1:]
    assert service.data("GET", f"/api/session/ses_api/message/{every[1]['id']}") == every[1]
    assert service.data("GET", "/api/session/ses_api/inbox") == []
    service.call("POST", "/api/session/ses_api/compact")
    compacted = service.settle("ses_api")
    assert [m["type"] for m in compacted][-2:] == ["compaction", "idle"]
    record = service.data("GET", "/api/session/ses_api")
    assert isinstance(record, dict) and record["metadata"] == {"blizzard": {"run": "r1"}}
    service.call("PATCH", "/api/session/ses_api", {"metadata": {"blizzard": {"run": "r2"}}})
    patched = service.data("GET", "/api/session/ses_api")
    assert isinstance(patched, dict) and patched["metadata"] == {"blizzard": {"run": "r2"}}
    status, _ = service.call("POST", "/api/session", {"id": "ses_api", "location": {"directory": str(tmp_path)}})
    assert status == 409
    models = objects(service.data("GET", "/api/model"))
    assert all(objects(model["variants"]) for model in models)
    status, info = service.call("GET", "/api/info")
    assert status == 200 and isinstance(info, dict) and info["version"] == "2.1.4"


def test_a_steer_taken_at_a_step_boundary_ends_that_step_once(tmp_path: Path, start_service: Starter) -> None:
    service = _started(start_service, tmp_path)
    repo = _repo(tmp_path)
    service.open_session("ses_steer_step", repo)
    script = (
        "import time\nfor i in range(400):\n    open('tick', 'w').write(str(i))\n    time.sleep(0.05)\n    step()\n"
    )
    with service.events() as events:
        service.data("POST", "/api/session/ses_steer_step/prompt", {"text": script})
        _wait_for(lambda: (repo / "tick").exists())
        service.data("POST", "/api/session/ses_steer_step/prompt", {"text": "text('STEERED')", "delivery": "steer"})
        messages = service.settle("ses_steer_step")
    ended = [
        str(data["assistantMessageID"])
        for e in events
        if e.get("type") == "session.step.ended" and is_json(data := e["data"])
    ]
    assert len(ended) == len(set(ended))
    steps = _assistants(messages)
    assert all(step["finish"] == "tool-calls" for step in steps[:-1])
    assert steps[-1]["finish"] == "stop" and _texts(steps[-1]) == ["STEERED"]


def test_a_command_reading_stdin_sees_its_end_rather_than_the_services_pipe(
    tmp_path: Path, start_service: Starter
) -> None:
    service = _started(start_service, tmp_path)
    service.open_session("ses_stdin", _repo(tmp_path))
    messages = _turn(
        service, "ses_stdin", "import sys\nr = shell('cat; echo EOF')\ntext(r.output.strip() + sys.stdin.read())\n"
    )
    assert _texts(_assistants(messages)[-1]) == ["EOF"]
