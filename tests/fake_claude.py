#!/usr/bin/env python3
"""A stand-in for `claude` in tests: it writes the session file Claude Code writes, notes how it was
started, and sleeps. It talks to no service. Linux only (it sets its program name to "claude")."""
import ctypes
import json
import os
import sys
import time
import uuid

try:
    ctypes.CDLL(None).prctl(15, b"claude", 0, 0, 0)   # PR_SET_NAME, so `ps` shows "claude"
except Exception:
    pass
a = sys.argv[1:]
home = os.environ["HOME"]
names_path = os.path.join(home, "names.json")
try:
    names = json.load(open(names_path))
except (OSError, ValueError):
    names = {}
if "--resume" in a:
    sid = a[a.index("--resume") + 1]
else:
    sid = os.environ.get("FAKE_SID") or str(uuid.uuid4())
    names[sid] = os.environ.get("FAKE_NAME", "")
    json.dump(names, open(names_path, "w"))
d = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"), "sessions")
os.makedirs(d, exist_ok=True)
now = int(time.time() * 1000)
json.dump({"pid": os.getpid(), "sessionId": sid, "cwd": os.getcwd(), "name": names.get(sid, ""),
           "kind": os.environ.get("FAKE_KIND", "interactive"), "entrypoint": "cli", "startedAt": now,
           "status": os.environ.get("FAKE_STATUS", "idle"), "updatedAt": now}, open(os.path.join(d, f"{os.getpid()}.json"), "w"))
with open(os.path.join(home, "launches.jsonl"), "a") as f:
    f.write(json.dumps({"pid": os.getpid(), "sid": sid, "argv": a, "cwd": os.getcwd(),
                        "child": "CLAUDE_CODE_CHILD_SESSION" in os.environ,
                        "claude_env": sorted(k for k in os.environ if k.startswith("CLAUDE"))}) + "\n")
time.sleep(900)
