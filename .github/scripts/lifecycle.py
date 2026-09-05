import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import re
import sys

from tencent_api import call, workspaces


MANAGED_NAME = "keepalive-boot"
MANAGED_COMMAND = "bash /workspace/.keepalive/boot.sh lifecycle"


def normalize(value):
    if not isinstance(value, dict):
        raise ValueError("Lifecycle must be a JSON object")
    phases = {"init": "Init", "start": "Start", "destroy": "Destroy"}
    result = {phase: [] for phase in phases.values()}
    seen = set()
    for key, commands in value.items():
        phase = phases.get(key.lower())
        if not phase or phase in seen or not isinstance(commands, list):
            raise ValueError("Invalid or duplicate lifecycle phase")
        seen.add(phase)
        for command in commands:
            if not isinstance(command, dict):
                raise ValueError("Lifecycle commands must be objects")
            fields = {name.lower(): content for name, content in command.items()}
            if len(fields) != len(command) or set(fields) != {"name", "command"}:
                raise ValueError("Each lifecycle command requires only Name and Command")
            if any(not isinstance(content, str) or not content.strip() for content in fields.values()):
                raise ValueError("Lifecycle names and commands must be nonempty strings")
            result[phase].append({"Name": fields["name"], "Command": fields["command"]})
    return result


def with_startup(baseline):
    desired = copy.deepcopy(baseline)
    existing = [item for item in desired["Start"] if item["Name"] == MANAGED_NAME]
    if len(existing) > 1 or any(item["Command"] != MANAGED_COMMAND for item in existing):
        raise ValueError("The keepalive-boot lifecycle name is already used by another command")
    if not existing:
        desired["Start"].append({"Name": MANAGED_NAME, "Command": MANAGED_COMMAND})
    return desired


def select_workspace(space):
    rows = [row for row in workspaces() if row.get("Status", "").upper() != "INVALID"]
    if space:
        rows = [row for row in rows if row.get("SpaceKey") == space]
    if len(rows) != 1:
        raise ValueError("Specify one existing space_key when the account has multiple workspaces")
    selected = rows[0].get("SpaceKey", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", selected):
        raise ValueError("Invalid workspace SpaceKey")
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "apply", "restore"))
    parser.add_argument("--space", default="")
    parser.add_argument("--plan", required=True)
    arguments = parser.parse_args()
    path = Path(arguments.plan)
    if arguments.operation == "prepare":
        raw = os.environ.get("LIFECYCLE_BASELINE", "").strip()
        if not raw:
            raise ValueError("Provide current lifecycle JSON; use {} only after confirming there are no existing hooks")
        baseline = normalize(json.loads(raw))
        desired = with_startup(baseline)
        selected = select_workspace(arguments.space)
        plan = {
            "version": 1,
            "space_key": selected,
            "prepared_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "baseline_source": "operator-confirmed",
            "original": baseline,
            "desired": desired,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(plan, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.chmod(path, 0o600)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"space={selected}\n")
        print(f"Prepared lifecycle plan for {selected}; no workspace changes made")
        return
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("version") != 1 or plan.get("baseline_source") != "operator-confirmed":
        raise ValueError("Unsupported or unconfirmed lifecycle plan")
    selected = select_workspace(plan["space_key"])
    original = normalize(plan["original"])
    desired = normalize(plan["desired"])
    if desired != with_startup(original):
        raise ValueError("Lifecycle plan contains unexpected changes")
    lifecycle = desired if arguments.operation == "apply" else original
    result = call("ModifyWorkspace", {"SpaceKey": selected, "Lifecycle": lifecycle})
    print(f"Lifecycle {arguments.operation} accepted for {selected}; RequestId={result.get('RequestId')}")
    print("No workspace restart requested. A successful API response does not prove startup execution.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Lifecycle operation failed: {error}", file=sys.stderr)
        sys.exit(1)
