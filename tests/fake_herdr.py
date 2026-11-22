#!/usr/bin/env python3
"""A stand-in for the `herdr` command in tests. It answers from HOME/herdr-state.json and writes down
every `pane run`, `tab create`, `workspace create` and `tab rename` in HOME/herdr-runs.jsonl. It talks to no service."""
import json
import os
import sys

home = os.environ["HOME"]
a = sys.argv[1:]
state = json.load(open(os.path.join(home, "herdr-state.json")))


def out(result):
    print(json.dumps({"result": result}))


def note(d):
    with open(os.path.join(home, "herdr-runs.jsonl"), "a") as f:
        f.write(json.dumps(d) + "\n")


if a[:2] == ["pane", "list"]:
    out({"panes": state["panes"]})
elif a[:2] == ["tab", "list"]:
    out({"tabs": state["tabs"]})
elif a[:2] == ["workspace", "list"]:
    out({"workspaces": state["workspaces"]})
elif a[:2] == ["pane", "process-info"]:
    out({"process_info": state["process_info"].get(a[a.index("--pane") + 1], {})})
elif a[:2] == ["pane", "read"]:
    sys.stdout.write(state["visible"].get(a[2], ""))
elif a[:2] == ["pane", "run"]:
    note({"pane": a[2], "cmd": a[3]})
    out({})
elif a[:2] == ["tab", "create"]:
    d = {"create": a[a.index("--label") + 1], "workspace": a[a.index("--workspace") + 1]}
    if "--no-focus" in a:
        d["no_focus"] = True
    note(d)
    out({"root_pane": {"pane_id": "new-1"}})
elif a[:2] == ["workspace", "create"]:
    lab = a[a.index("--label") + 1]
    note({"create_ws": lab, "no_focus": "--no-focus" in a})
    if lab in state.get("ws_fail", []):
        sys.exit(1)
    out({"workspace": {"workspace_id": f"wn-{lab}"}, "tab": {"tab_id": f"wst-{lab}"}, "root_pane": {"pane_id": f"wsp-{lab}"}})
elif a[:2] == ["notification", "show"]:
    note({"notification": a[2], "body": a[a.index("--body") + 1]})
    out({})
elif a[:2] == ["tab", "rename"]:
    note({"rename": a[2], "label": " ".join(a[3:])})
    out({})
else:
    sys.exit(2)
