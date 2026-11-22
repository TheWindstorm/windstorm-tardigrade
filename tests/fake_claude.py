#!/usr/bin/env python3
"""A stand-in for `claude` in tests: it writes the session file Claude Code writes, notes how it was
started and what is typed into it, and waits. It talks to no service. Linux only (it sets its program
name to "claude").

Files in HOME change how it behaves: `slow` (seconds to "load"), `trustprompt` (stop at the folder
question first), `failonce` (end at once, one time), `status` (the status it reports, default idle)."""
import ctypes
import json
import os
import select
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
                        "claude_env": sorted(k for k in os.environ if k.startswith("CLAUDE")),
                        "seen": {k: os.environ[k] for k in ("DISPLAY", "MY_SETTING", "MY_SECRET_TOKEN", "ACCT_NAME",
                                                           "CLAUDE_CODE_AUTO_COMPACT_WINDOW") if k in os.environ}}) + "\n")
if os.path.exists(os.path.join(home, "failonce")):
    os.remove(os.path.join(home, "failonce"))
    sys.exit(1)                         # a start that fails, as when the network is not up yet
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
try:
    status = open(os.path.join(home, "status")).read().strip() or "idle"
except OSError:
    status = os.environ.get("FAKE_STATUS", "idle")
d = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"), "sessions")
os.makedirs(d, exist_ok=True)
now = int(time.time() * 1000)
json.dump({"pid": os.getpid(), "sessionId": sid, "cwd": os.getcwd(), "name": names.get(sid, ""),
           "kind": os.environ.get("FAKE_KIND", "interactive"), "entrypoint": "cli", "startedAt": now,
           "status": status, "updatedAt": now}, open(os.path.join(d, f"{os.getpid()}.json"), "w"))
# whatever is typed into this "session" is written down, one line each
end = time.time() + 900
while time.time() < end:
    try:
        ready = select.select([sys.stdin], [], [], 5)[0]
        if not ready:
            continue
        line = sys.stdin.readline()
    except (OSError, ValueError):
        break
    if not line:
        time.sleep(max(0, end - time.time()))     # no terminal: just stay alive
        break
    with open(os.path.join(home, "typed.jsonl"), "a") as f:
        f.write(json.dumps({"sid": sid, "text": line.rstrip("\r\n")}) + "\n")
