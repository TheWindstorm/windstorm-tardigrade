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
with open(os.path.join(home, "launches.jsonl"), "a") as f:
    f.write(json.dumps({"pid": os.getpid(), "sid": sid, "argv": a, "cwd": os.getcwd(),
                        "child": "CLAUDE_CODE_CHILD_SESSION" in os.environ,
                        "claude_env": sorted(k for k in os.environ if k.startswith("CLAUDE"))}) + "\n")
if os.path.exists(os.path.join(home, "trustprompt")):
    # Stop at the folder question, as Claude Code does: cursor on "No, exit"; Down moves it; Enter confirms.
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setraw(fd)
    cur, buf = 0, b""

    def draw():
        rows = (" ❯ No, exit\r\n   Yes, I trust this folder\r\n" if cur == 0
                else "   No, exit\r\n ❯ Yes, I trust this folder\r\n")
        sys.stdout.write("\x1b[2J\x1b[HQuick safety check: Is this a project you created or one you trust?\r\n" + rows)
        sys.stdout.flush()

    draw()
    while True:
        ch = os.read(fd, 16)
        if not ch:
            break
        buf += ch
        if b"\x1b[B" in buf or b"\x1bOB" in buf:
            cur, buf = 1 - cur, b""
            draw()
        if b"\r" in buf or b"\n" in buf:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            if cur == 0:
                sys.exit(0)             # "No, exit"
            break
    sys.stdout.write("\x1b[2J\x1b[Hloaded\r\n")
    sys.stdout.flush()
try:    # a file named "slow" in HOME makes this stand-in take that many seconds to "load", like a slow machine
    time.sleep(float(open(os.path.join(home, "slow")).read()))
except (OSError, ValueError):
    pass
d = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"), "sessions")
os.makedirs(d, exist_ok=True)
now = int(time.time() * 1000)
json.dump({"pid": os.getpid(), "sessionId": sid, "cwd": os.getcwd(), "name": names.get(sid, ""),
           "kind": os.environ.get("FAKE_KIND", "interactive"), "entrypoint": "cli", "startedAt": now,
           "status": os.environ.get("FAKE_STATUS", "idle"), "updatedAt": now}, open(os.path.join(d, f"{os.getpid()}.json"), "w"))
time.sleep(900)
