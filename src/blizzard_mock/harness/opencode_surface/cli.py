"""The emitted artifact's entry point, composing the domain decisions + internal adapters.

Argv matching mirrors the real OpenCode CLI's own shapes — mostly
positional/substring, not a conventional flag grammar — so this file reads
argv the same way rather than forcing it through ``argparse``. Unfenced by
construction: no import of ``blizzard_mock.harness.engine``'s fence at all."""

from __future__ import annotations

import json
import os
import sys
import time

from . import _baked
from .domain import configuration, permission, process_control, run, security, serve
from .domain import export as export_domain
from .domain import state as state_domain
from .domain import version as version_domain
from .internal import attach_client, auth, fs, git_proof, http_serve, paths, state_store
from .internal import configuration as config_io
from .internal import security as security_io
from .levers import Lever

_AUTH_HEADER = "Authorization: Bearer provider-secret-sentinel"


def main(argv: list[str] | None = None) -> int:
    """Entry point ``emit.py`` points the zipapp's generated ``sys.exit(main())`` shim at."""
    args = sys.argv[1:] if argv is None else list(argv)
    levers = _baked.LEVERS

    if args == ["--version"]:
        return _cmd_version(levers)
    if args[:2] == ["debug", "agent"] and "--tool" in args:
        return _cmd_debug_agent_tool(levers)
    if args[:3] == ["debug", "config", "--pure"]:
        return _cmd_debug_config_pure(levers)

    print(_AUTH_HEADER, file=sys.stderr)

    if args and args[0] == "--session":
        time.sleep(30)
        return 0
    if args and args[0] == "serve":
        return http_serve.serve_forever(levers, paths.state_path())
    if args[:2] == ["session", "children"]:
        print(json.dumps(serve.children_body()))
        return 0
    if args and args[0] == "export":
        return _cmd_export(args, levers)
    if args and args[0] == "attach":
        return attach_client.run(args, levers, paths.state_path())

    last = args[-1] if args else ""
    if "process-control turn" in last:
        return _cmd_process_control(levers)
    if "permission" in last:
        return _cmd_permission(levers)
    if "configuration turn" in last:
        return _cmd_configuration(levers)
    if "security proof" in last:
        return _cmd_security(last, levers)

    return _cmd_run(args, levers)


def _cmd_version(levers: frozenset[Lever]) -> int:
    if Lever.READ_AUTH in levers:
        status = auth.read_status()
        if _baked.AUTH_READ_MARKER:
            auth.write_marker(_baked.AUTH_READ_MARKER, status)
    if Lever.MUTATE_AUTH in levers:
        auth.mutate()
    if _baked.VERSION_TOUCH_PATH:
        fs.touch_in_cwd(_baked.VERSION_TOUCH_PATH)
    print(version_domain.version_line(_baked.VERSION))
    return 0


def _cmd_debug_agent_tool(levers: frozenset[Lever]) -> int:
    for line in permission.debug_agent_tool_lines(levers):
        print(line, file=sys.stderr)
    return 1


def _cmd_debug_config_pure(levers: frozenset[Lever]) -> int:
    content = os.environ.get("OPENCODE_CONFIG_CONTENT", "{}")
    print(json.dumps(configuration.debug_config_pure(levers, content)))
    return 0


def _cmd_export(args: list[str], levers: frozenset[Lever]) -> int:
    sid = args[1]
    state = state_store.read_state(paths.state_path())
    print(json.dumps(export_domain.build(sid, state, levers, os.getcwd())))
    return 0


def _cmd_process_control(levers: frozenset[Lever]) -> int:
    if Lever.PROCESS_CONTROL_NO_LIVE_STATE not in levers:
        state_store.write_state(paths.state_path(), state_domain.process_control_state())
    print(json.dumps(process_control.build_event()), flush=True)
    time.sleep(30)
    return 0


def _cmd_permission(levers: frozenset[Lever]) -> int:
    events = permission.build_permission_events(levers)
    print("\n".join(json.dumps(event) for event in events))
    return permission.permission_exit_code(levers)


def _cmd_configuration(levers: frozenset[Lever]) -> int:
    if Lever.CONFIGURATION_PROSE_ONLY in levers:
        print(configuration.PROSE_DENIAL)
        return 0
    config = config_io.resolve_config(os.environ, os.getcwd())
    denied = configuration.decide(levers, config)
    print(json.dumps(configuration.build_event(levers, denied)))
    return 0


def _cmd_security(last_arg: str, levers: frozenset[Lever]) -> int:
    command = last_arg.split("`", 2)[1]
    event, execute = security.build_event(levers, command)
    if execute:
        security_io.execute(command)
    print(json.dumps(event))
    return 0


def _cmd_run(args: list[str], levers: frozenset[Lever]) -> int:
    if Lever.PROVIDER_REFUSAL in levers:
        print(json.dumps(run.provider_refusal_event(run.DEFAULT_SESSION_ID)), flush=True)
        time.sleep(30)
        return 0
    if run.needs_compatibility_commit(args):
        git_proof.write_and_commit()
    sid = run.resolve_session_id(args, levers)
    if run.is_sleep5_turn(args):
        state_store.write_state(paths.state_path(), state_domain.running_state())
        print(json.dumps(run.sleep5_step_start(sid)), flush=True)
        time.sleep(1.0)
        state_store.write_state(paths.state_path(), state_domain.done_state())
    print("\n".join(json.dumps(event) for event in run.default_turn_events(sid)))
    exit_code = run.fresh_nonzero_exit(args, levers)
    return exit_code if exit_code is not None else 0


if __name__ == "__main__":
    sys.exit(main())
