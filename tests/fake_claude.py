#!/usr/bin/env python3
"""A stand-in for `claude` in tests: it writes the session file Claude Code writes, notes how it was
started and what is typed into it, and waits. It talks to no service. Linux only (it sets its program
name to "claude").

Files in HOME change how it behaves: `slow` (seconds to "load"), `trustprompt` (stop at the folder
question first), `usagebox` (never load: redraw an old conversation that quotes the folder question, with
another dialog open under it, and write down every key received), `failonce` (end at once, one time),
`status` (the status it reports, default idle), `noconv` (session ids it has "no conversation" for), `noconvslow` (the same, said only after 6 s),
`failsid` (session ids whose start always fails), `limitnotice` (show the usage-limit notice under
the empty box), `limitqueue` (a line typed in meets the weekly limit and is held for the reset), `oldlimit` (session ids whose
redrawn history shows an old limit reply above a newer short turn), `tallstatus` (that many status-line
lines drawn at the very foot, under the box and any notice), `psfailon` (session ids whose start, unless
started by hand with MY_SETTING=byhand, makes the test's stand-in `ps` fail from that moment: HOME/psfail),
`clockshift` (G5.14, T75: the family's clock shift, LD_PRELOAD and SHIFT_SECONDS, one per line; the suite writes it
when it runs shifted, and a stand-in the tool started with its clean environment takes it from here, so the tool's
own environment stays what it is for a person)."""
import ctypes
import json
import os
import select
import sys
import time
import uuid

_cs = os.path.join(os.environ.get("HOME", ""), "clockshift")
if "SHIFT_SECONDS" not in os.environ and os.path.exists(_cs):
    with open(_cs) as _f:
        _pre, _sec = _f.read().split("\n")[:2]
    os.execve(os.path.abspath(sys.argv[0]), sys.argv, dict(os.environ, LD_PRELOAD=_pre, SHIFT_SECONDS=_sec))

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
def listed(name, sid):
    try:
        return sid in open(os.path.join(home, name)).read()
    except OSError:
        return False


if "--resume" in a:
    sid = a[a.index("--resume") + 1]
else:
    sid = os.environ.get("FAKE_SID") or str(uuid.uuid4())
    names[sid] = a[a.index("--name") + 1] if "--name" in a else os.environ.get("FAKE_NAME", "")
    json.dump(names, open(names_path, "w"))
with open(os.path.join(home, "launches.jsonl"), "a") as f:
    f.write(json.dumps({"pid": os.getpid(), "sid": sid, "argv": a, "cwd": os.getcwd(),
                        "child": "CLAUDE_CODE_CHILD_SESSION" in os.environ,
                        "claude_env": sorted(k for k in os.environ if k.startswith("CLAUDE")),
                        "path": os.environ.get("PATH", ""), "now": int(time.time()),
                        "seen": {k: os.environ[k] for k in ("DISPLAY", "MY_SETTING", "MY_SECRET_TOKEN", "ACCT_NAME", "PWD", "INVOCATION_ID", "JOURNAL_STREAM",
                                                           "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "SHIFT_SECONDS", "TG_NOT_HANDED")
                                 if k in os.environ}}) + "\n")
if listed("psfailon", sid) and os.environ.get("MY_SETTING") != "byhand":
    open(os.path.join(home, "psfail"), "w").close()
if "--resume" in a and listed("noconv", sid):
    print(f"No conversation found with session ID: {sid}")     # what Claude Code says for a session nobody wrote in
    sys.exit(1)
if "--resume" in a and listed("noconvslow", sid):
    time.sleep(6)                       # a slow machine: it says so only after the run has counted it up
    print(f"No conversation found with session ID: {sid}")
    sys.exit(1)
if listed("failsid", sid):
    sys.exit(1)                         # a session whose start keeps failing
if os.path.exists(os.path.join(home, "failonce")):
    os.remove(os.path.join(home, "failonce"))
    sys.exit(1)                         # a start that fails, as when the network is not up yet
if os.path.exists(os.path.join(home, "usagebox")):
    # A resumed session redraws its old conversation. This one quoted the folder question, cursor mark and
    # all. Under it another dialog is open. Every key that arrives is written down; this never "loads".
    import termios
    import tty
    tty.setraw(sys.stdin.fileno())
    sys.stdout.write("\x1b[2J\x1b[H● earlier in this conversation: the question was \"trust this folder?\"\r\n"
                     "   No, exit\r\n ❯ 2. Yes, I trust this folder\r\n Enter to confirm · Esc to cancel\r\n\r\n"
                     " You have reached your plan's usage limit.\r\n What do you want to do?\r\n"
                     " ❯ 1. Stop and wait\r\n   2. Switch to usage credits\r\n")
    sys.stdout.flush()
    while True:
        ch = os.read(sys.stdin.fileno(), 64)
        if not ch:
            break
        with open(os.path.join(home, "keys.log"), "a") as f:
            f.write(ch.hex() + "\n")
if os.path.exists(os.path.join(home, "trustprompt")):
    # Stop at the folder question, as Claude Code does: cursor on "No, exit"; Down moves it; Enter confirms.
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setraw(fd)
    cur, buf = 0, b""

    def draw():      # the text Claude Code 2.1.330 shows
        rows = (" ❯ No, exit\r\n   Yes, I trust this folder\r\n" if cur == 0
                else "   No, exit\r\n ❯ Yes, I trust this folder\r\n")
        sys.stdout.write("\x1b[2J\x1b[H " + os.getcwd() + "\r\n Quick safety check: Is this a project you created or "
                         "one you trust?\r\n Claude Code'll be able to read, edit, and execute files here.\r\n"
                         " Security guide\r\n" + rows + " Enter to confirm · Esc to cancel\r\n")
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
           "kind": os.environ.get("FAKE_KIND", "interactive"), "startedAt": now,
           # Claude Code 2.1.330 marks a print-mode run "interactive" too; only its entrypoint differs
           "entrypoint": "sdk-cli" if ("-p" in a or "--print" in a) else "cli",
           "status": status, "updatedAt": now}, open(os.path.join(d, f"{os.getpid()}.json"), "w"))
# its input box at the foot of the screen, empty, as Claude Code draws it when idle
if not os.path.exists(os.path.join(home, "nobox")):
    if listed("oldlimit", sid):     # its redrawn history: a limit reply from before the reset, then a short turn
        sys.stdout.write("  \u23bf  You've hit your weekly limit \u00b7 resets 12pm\r\n\u23fa Usage limit reached \u00b7 "
                         "set to continue automatically at 12pm\r\n\r\n\u276f hello again\r\n\r\n\u23fa Hello.\r\n")
    sys.stdout.write("\r\n" + "─" * 40 + "\r\n❯ \r\n" + "─" * 40 + "\r\n  ⏵⏵ bypass permissions on\r\n")
    if os.path.exists(os.path.join(home, "limitnotice")):     # what Claude Code 2.1.330 shows at the weekly limit
        sys.stdout.write("  ⚠ Usage limit reached · limit resets 12pm\r\n    Continuing automatically at 12pm · "
                         "Esc to cancel · /usage-credits to\r\n    continue now\r\n")
    try:    # a person's own status line, as tall as the number in `tallstatus`, under everything else
        for i in range(int(open(os.path.join(home, "tallstatus")).read())):
            sys.stdout.write(f"  STATUS{i} folder · branch · context 4% · week 17% used · cpu 6%\r\n")
    except (OSError, ValueError):
        pass
    sys.stdout.flush()
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
    if os.path.exists(os.path.join(home, "limitqueue")):    # what Claude Code 2.1.330 shows: the message is held
        sys.stdout.write("  \u23bf  You've hit your weekly limit \u00b7 resets 12pm\r\n\u23fa Usage limit reached \u00b7 "
                         "set to continue automatically at 12pm\r\n")
        sys.stdout.flush()
