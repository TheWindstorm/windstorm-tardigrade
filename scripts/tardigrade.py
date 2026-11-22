#!/usr/bin/env python3
"""Windstorm Tardigrade: bring Claude Code sessions back after a reboot or a crash, and say what is running.

  record              write down every live session (a timer runs this every 5 minutes)
  revive [--boot]     start every recorded session that is not running (runs once after boot)
  revive --only NAME  bring back just that one
  revive --dry-run --all   show the start line of every recorded session, as if none ran
                      (add --start-file to also print each start file, to the screen only)
  trust on|off        opt in (or out): after a restart, answer Claude Code's "trust this folder?"
                      for a session's own folder. Off unless you switch it on.
  census [--json]     what is running, what runs with no window open, what is down
  open                open a terminal window for each hidden session
  setup / uninstall   install or remove the boot service and the record timer
  --dry-run           with record, revive, open or setup: say what would happen, change nothing

Rules it keeps:
  * A session comes back as itself: `claude --resume <its own id>` in its own folder, with its own
    model, effort and permission mode. Its name and its whole conversation come back with it.
  * Never a second copy. A session id that any live process on this machine holds is never started.
  * It never ends a session and never answers a dialog for you (one exception, only if you switch it
    on: `trust on`). It types one thing into a session, and only into one it started itself: its
    wake-up note, once that session is loaded and idle. `"wake": "idle"` switches the note off.
  * Nothing but the start command is ever on a command line: the note is typed, and the settings a
    session needs are in a start file only you can read. A command line is what a broad, match-by-text
    kill command is matched against.
  * It never stores a token or a key. For a login token it keeps the variable's name and an 8-digit
    one-way fingerprint, so it can tell whether a session came back on the same login.
  * The record is never overwritten by a half-started machine, and never by an empty list. Sessions
    that vanish together (a crash) stay on the record for some hours, so they still come back.
  * Other users' sessions on a shared machine are counted, never read.

Standard library only. Record: ~/.config/windstorm-tardigrade/lanes.json
Log: ~/.local/state/windstorm-tardigrade/tardigrade.log
"""
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time

VERSION = "0.1.7"
HOME = os.path.expanduser("~")
MAC = sys.platform == "darwin"
CFG_DIR = os.path.join(HOME, ".config", "windstorm-tardigrade")
STATE = os.path.join(HOME, ".local", "state", "windstorm-tardigrade")
TABLE = os.path.join(CFG_DIR, "lanes.json")
LOG = os.path.join(STATE, "tardigrade.log")
INSTALL_DIR = os.path.join(HOME, ".local", "share", "windstorm-tardigrade")
ARGS = sys.argv[2:]
DRY = "--dry-run" in ARGS
TESTING = os.environ.get("TARDIGRADE_TESTING") == "1"

CFG = {
    "shell": None,            # "bash" or "zsh"; default: your login shell
    "tmux": None,             # path to tmux; default: found on PATH
    "herdr": None,            # path to herdr, if you use it
    "path": None,             # PATH for revived sessions; default: a standard one plus ~/.local/bin
    "tmux_tmpdir": None,      # set this if your shell sets TMUX_TMPDIR
    "boot_window_min": 40,    # "just booted" means: up for less than this
    "stagger_s": 3,           # pause between two session starts
    "settle_s": 8,            # wait this long before checking that a start worked
    "retry_min": 25,          # keep retrying failed starts this long (the network may lag at boot)
    "wake": "note",           # "note": tell each revived session what happened; "idle": say nothing
    "auto_revive": False,     # set by setup: the boot service is installed
    "herdr_tabs": True,       # re-attach Herdr tabs after a revive
    "attach_wait_min": 30,    # wait this long for the Herdr server after boot
    "down_keep_days": 14,     # how long a session that went down stays on the "down" list
    "skip_tmux_sockets": [],  # tmux socket names to leave alone, when another tool brings those sessions back
    "answer_trust": False,    # opt-in (`trust on`): answer "trust this folder?" for a revived session's own folder
    "watch_min": 15,          # after a boot, keep looking at the started sessions this long (slow machines load late)
    "claude_command": "claude",   # the command that starts Claude Code; the flags and --resume are added to it
    "account_env": "",        # name of a variable that holds a session's account label (a label, never a secret)
    "account_command": "",    # used in place of claude_command for a session with a label, e.g. "my-wrapper use {account}"
    "start_command": "",      # your own whole start line, in place of the three above; see the README for {placeholders}
    "env_file": "",           # NAME=value lines (or NUL-separated) added to a revived session's environment
    "tmux_conf": "",          # a tmux config file for a tmux server that Tardigrade has to start
    "server_command": "",     # your own command that starts the tmux server, run when none is up; {socket}
    "server_socket": "",      # the tmux socket name server_command is for; "" = any
    "carry_env": ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"],   # settings variables a session keeps across a revive
    "loose_socket": "tardigrade",   # tmux socket name where sessions that were not in tmux come back
    "loose_prefix": "",       # put before the tmux name of such a session
    "loose_skip": [],         # patterns: a session outside tmux whose name matches is not recorded ("^$" = no name)
    "herdr_default_workspace": "",  # Herdr workspace for a session that has a tab label and no workspace
    "tsv_path": "",           # also write the record as a tab-separated table (eight columns) at this path
    "tsv_socket": "",         # only sessions on this tmux socket name go in that table; "" = all of them
    "load_max": 0,            # 0 = off; else wait before each start while the 1-minute load is above this
    "load_wait_s": 120,       # the longest such wait, per session
    "lost_keep_h": 12,        # sessions that vanished together stay revivable this long
}

WAKE_NOTE = (
    "Windstorm Tardigrade: this machine restarted and your session was brought back at {when}. "
    "Your conversation is intact. Anything you had running in the background is gone: timers, "
    "watchers, background commands and booked slots. Read your handoff or notes if you keep them, "
    "check what was in progress, start again only what is still needed, then carry on where you "
    "left off. If nothing was in progress, say so in one line and wait."
)

TOKEN_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# a variable whose name looks like this is never recorded, carried or copied, whatever the settings say
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSW|CREDENTIAL|API_?KEY|PRIVATE_?KEY", re.I)
NAME_OK = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SAFE_VALUE = re.compile(r"[\w.:/@%+,=~ -]{1,200}")
# set by a Claude session for the programs it starts; CLAUDE_CODE_CHILD_SESSION turns transcript saving off
CHILD_VARS = ("CLAUDECODE CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_SESSION_ID CLAUDE_PID CLAUDE_EFFORT "
              "CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_SESSION_ATTENDED "
              "CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN")
COLS = ["session", "session_id", "cwd", "model", "effort", "mode", "tab_label", "workspace_label"]
# never taken from an env_file: they belong to the shell that is starting, not to the one the file came from
NEVER_CARRY = {"PWD", "OLDPWD", "UID", "EUID", "PPID", "SHLVL", "_", "TERM", "TMUX", "TMUX_PANE", "BASHOPTS", "SHELLOPTS"}
TERMINALS = ("gnome-terminal", "konsole", "kitty", "alacritty", "wezterm", "xterm", "tilix", "xfce4-terminal",
             "ptyxis", "kgx", "foot", "ghostty", "terminator", "iTerm", "Terminal", "Warp", "warp", "tabby")
IDES = ("code", "Code", "cursor", "Cursor", "zed", "idea", "pycharm", "webstorm", "goland", "windsurf")
SHELLS = ("bash", "zsh", "sh", "-bash", "-zsh", "fish", "dash")


# ---------- small tools ----------
def log(msg):
    os.makedirs(STATE, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(argv, timeout=30, env=None):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return 99, ""


def load_cfg():
    try:
        with open(os.path.join(CFG_DIR, "config.json"), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            CFG.update(d)
    except (OSError, ValueError):
        pass
    dirs = [os.path.join(HOME, ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    for k in ("tmux", "herdr"):
        if not CFG[k]:
            CFG[k] = shutil.which(k) or next(
                (os.path.join(d, k) for d in dirs if os.access(os.path.join(d, k), os.X_OK)), None)
    if not CFG["shell"]:
        sh = os.path.basename(os.environ.get("SHELL") or "")
        CFG["shell"] = sh if sh in ("bash", "zsh") else ("zsh" if MAC else "bash")
    if not CFG["path"]:
        CFG["path"] = ":".join(dirs + ["/usr/sbin", "/sbin"])
    if "trust_wait_min" in CFG:      # its name in 0.1.6
        CFG["watch_min"] = CFG["trust_wait_min"]


def plain_names(names):
    """the names from a setting that are safe to read from an environment: plain, and not a secret's"""
    return [n for n in (names or []) if isinstance(n, str) and NAME_OK.fullmatch(n) and not SECRET_NAME.search(n)]


def load_now():
    if TESTING and os.environ.get("TARDIGRADE_FAKE_LOAD"):
        return float(os.environ["TARDIGRADE_FAKE_LOAD"])
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


def save_cfg_key(key, value):
    os.makedirs(CFG_DIR, exist_ok=True)
    p = os.path.join(CFG_DIR, "config.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d[key] = value
    write_json(p, d)
    CFG[key] = value


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def ago(seconds):
    s = max(0, int(seconds))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} h"
    return f"{s // 86400} days"


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:48]


# ---------- processes ----------
def procs():
    """pid -> (parent pid, user id, program name). A process that has ended and only waits to be collected
    (a zombie) is left out: it holds no session."""
    rc, out = run(["ps", "-axo", "pid=,ppid=,uid=,stat=,comm="])
    res = {}
    for line in out.splitlines():
        p = line.split(None, 4)
        if len(p) == 5 and p[0].isdigit() and not p[3].startswith("Z"):
            res[int(p[0])] = (int(p[1]), int(p[2]), os.path.basename(p[4].strip()))
    return res


def is_claude(comm):
    # the native install runs a binary named after its version, such as 2.1.330
    return comm == "claude" or re.fullmatch(r"\d+\.\d+\.\d+", comm or "") is not None


def argv_of(pid):
    if not MAC:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return [x.decode(errors="replace") for x in f.read().split(b"\0") if x]
        except OSError:
            return []
    return run(["ps", "-o", "args=", "-p", str(pid)])[1].split()


def env_facts(pid):
    """Facts from a process's environment. The values kept are one folder path, an account label, and the
    settings variables named in carry_env; a name that looks like a secret's is never among them. A login
    token is reduced, in memory, to 8 hex digits of its SHA-256: enough to tell "same login or not",
    useless for getting the token back."""
    facts = {"child": False, "token_keys": [], "config_dir": "", "token_print": "", "account": "", "carry": {}}
    tok = []
    carry = plain_names(CFG.get("carry_env"))
    acct = (plain_names([CFG.get("account_env")]) or [""])[0]
    if MAC:
        pairs = run(["ps", "eww", "-o", "command=", "-p", str(pid)])[1].split()
    else:
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                pairs = [x.decode(errors="replace") for x in f.read().split(b"\0") if x]
        except OSError:
            return facts
    for kv in pairs:
        k, eq, v = kv.partition("=")
        if not eq:
            continue
        if k == "CLAUDE_CODE_CHILD_SESSION":
            facts["child"] = True
        elif k in TOKEN_KEYS and v:
            facts["token_keys"].append(k)
            tok.append(kv)
        elif k == "CLAUDE_CONFIG_DIR" and v:
            facts["config_dir"] = v
        elif k == acct and SAFE_VALUE.fullmatch(v):
            facts["account"] = v
        elif k in carry and SAFE_VALUE.fullmatch(v):
            facts["carry"][k] = v
    facts["token_keys"].sort()
    if tok:
        facts["token_print"] = hashlib.sha256(("tardigrade:" + "\n".join(sorted(tok))).encode()).hexdigest()[:8]
    return facts


def session_json(pid, config_dir=""):
    base = config_dir or os.path.join(HOME, ".claude")
    d = read_json(os.path.join(base, "sessions", f"{pid}.json"), {})
    return d if isinstance(d, dict) else {}


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def kids_of(P):
    kids = {}
    for p, (pp, _, _) in P.items():
        kids.setdefault(pp, []).append(p)
    return kids


def claude_under(pid, P):
    """the first Claude at or below this process, or None"""
    kids = kids_of(P)
    stack = [pid]
    while stack:
        p = stack.pop()
        if p in P and is_claude(P[p][2]):
            return p
        stack += kids.get(p, [])
    return None


# ---------- boot ----------
def boot_id():
    if TESTING and os.environ.get("TARDIGRADE_FAKE_BOOT_ID"):
        return os.environ["TARDIGRADE_FAKE_BOOT_ID"]
    if MAC:
        return re.sub(r"\D", "", run(["sysctl", "-n", "kern.boottime"])[1].split(",")[0])
    with open("/proc/sys/kernel/random/boot_id") as f:
        return f.read().strip()


def uptime_s():
    if TESTING and os.environ.get("TARDIGRADE_FAKE_UPTIME_S"):
        return float(os.environ["TARDIGRADE_FAKE_UPTIME_S"])
    if MAC:
        m = re.search(r"sec = (\d+)", run(["sysctl", "-n", "kern.boottime"])[1])
        return time.time() - int(m.group(1)) if m else 1e9
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def marker():
    return os.path.join(STATE, f"revived-{boot_id()}")


def in_boot_window():
    return uptime_s() < float(CFG["boot_window_min"]) * 60


# ---------- tmux ----------
def tmux_dir():
    return os.path.join(CFG.get("tmux_tmpdir") or os.environ.get("TMUX_TMPDIR") or "/tmp", f"tmux-{os.getuid()}")


def own_socket():
    """where a session that was not in tmux comes back"""
    return os.path.join(tmux_dir(), os.path.basename(str(CFG.get("loose_socket") or "tardigrade")))


def sockets():
    res = []
    try:
        names = sorted(os.listdir(tmux_dir()))
    except OSError:
        return res
    for n in names:
        p = os.path.join(tmux_dir(), n)
        try:
            if stat.S_ISSOCK(os.stat(p).st_mode):
                res.append(p)
        except OSError:
            pass
    return res


def tmux(sock, *a, timeout=10):
    if not CFG["tmux"]:
        return 99, ""
    return run([CFG["tmux"], "-S", sock, *a], timeout=timeout)


def panes():
    """pane pid -> (socket, tmux session name, windows attached). Read only."""
    res = {}
    for s in sockets():
        rc, out = tmux(s, "list-panes", "-a", "-F", "#{pane_pid}\t#{session_attached}\t#{session_name}", timeout=6)
        if rc != 0:
            continue
        for line in out.splitlines():
            p = line.split("\t", 2)
            if len(p) == 3 and p[0].isdigit():
                res[int(p[0])] = (s, p[2], int(p[1] or 0))
    return res


def has_session(sock, name):
    return os.path.exists(sock) and tmux(sock, "has-session", "-t", f"={name}")[0] == 0


# ---------- what is running ----------
def where(pid, P, PN):
    """Where a session lives: (kind, detail). kind: tmux, herdr, terminal, ide, ssh or none."""
    p, steps = pid, 0
    while p in P and p > 1 and steps < 40:
        if p in PN:
            return "tmux", PN[p]
        if p != pid:
            comm = P[p][2]
            if comm == "herdr":
                return "herdr", None
            if comm.startswith(TERMINALS):
                return "terminal", comm
            if comm.startswith(IDES):
                return "ide", comm
            if comm.startswith("sshd"):
                return "ssh", None
        p = P[p][0]
        steps += 1
    return "none", None


def last_model(sid, since_ms, config_dir=""):
    """(model used since this run started, last model in the transcript's tail)"""
    import datetime
    base = config_dir or os.path.join(HOME, ".claude")
    for f in glob.glob(os.path.join(base, "projects", "*", f"{sid}.jsonl")):
        try:
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                n = fh.tell()
                fh.seek(max(0, n - 600_000))
                tail = fh.read().decode(errors="replace")
        except OSError:
            continue
        since, last = None, None
        for line in tail.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            m, msg = None, d.get("message") or {}
            if not isinstance(msg, dict):
                continue
            if d.get("type") == "assistant" and str(msg.get("model", "")).startswith("claude-"):
                m = msg["model"]
            c = msg.get("content")
            if isinstance(c, str) and "<command-name>/model</command-name>" in c:
                a = re.search(r"<command-args>([^<]+)</command-args>", c)
                if a:
                    m = a.group(1).strip()
            if not m:
                continue
            last = m
            try:
                t = datetime.datetime.fromisoformat(str(d.get("timestamp", "")).replace("Z", "+00:00")).timestamp() * 1000
            except ValueError:
                t = 0
            if t >= since_ms:
                since = m
        return since, last
    return None, None


def launch_facts(pid, sj, config_dir):
    a = argv_of(pid)

    def opt(name):
        return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else ""

    model = opt("--model")
    since, last = last_model(sj.get("sessionId", ""), sj.get("startedAt") or 0, config_dir)
    if since and since != re.sub(r"\[1m\]$", "", model):
        model = since      # the session changed model during this run
    elif not model and last:
        model = last       # started without --model: keep what its conversation last ran on
    mode = "bypass" if "--dangerously-skip-permissions" in a else (opt("--permission-mode") or "default")
    add_dirs = []
    if "--add-dir" in a:
        for x in a[a.index("--add-dir") + 1:]:
            if x.startswith("-"):
                break
            add_dirs.append(x)
    return {"model": model, "effort": opt("--effort"), "mode": mode, "add_dirs": add_dirs}


def live(with_launch=True):
    """(my live sessions, number of other users' Claude processes, number of helper sessions)"""
    P, PN = procs(), panes()
    me, mine, others, helpers = os.getuid(), [], 0, 0
    table = read_json(TABLE, {})
    known = {r["session_id"]: r for r in (table.get("down", []) + table.get("lanes", []))} if isinstance(table, dict) else {}
    for pid, (_, uid, comm) in sorted(P.items()):
        if not is_claude(comm):
            continue
        if uid != me:
            others += 1        # counted, never read
            continue
        ef = env_facts(pid)
        sj = session_json(pid, ef["config_dir"])
        sid = sj.get("sessionId")
        starting = False
        if not sid:
            # No session file yet. A process started with --resume for a session on our record is one that
            # Claude Code is still loading (on a slow machine just after boot this takes minutes): it is
            # running, not down. Anything else without a session file is not a session of ours.
            m = re.search(r"--resume[ =]([0-9a-f-]{36})", " ".join(argv_of(pid)))
            if not m or m.group(1) not in known:
                continue
            sid, starting = m.group(1), True
        if starting or not sj.get("status"):
            k = known.get(sid, {})     # until it has loaded, its name and folder are the recorded ones
            sj = dict(sj, name=k.get("name") or sj.get("name") or "", cwd=sj.get("cwd") or k.get("cwd") or HOME,
                      status="starting", sessionId=sid)
        kind, detail = where(pid, P, PN)
        interactive = sj.get("kind", "interactive") == "interactive"
        if ef["child"] or not interactive:
            helpers += 1       # started by another session, or not interactive: not a lane
            continue
        row = {
            "pid": pid, "session_id": sid, "name": (sj.get("name") or "").strip(),
            "cwd": sj.get("cwd") or HOME, "host": kind, "config_dir": ef["config_dir"],
            "token_keys": ef["token_keys"], "token_print": ef["token_print"], "entrypoint": sj.get("entrypoint", "cli"),
            "account": ef["account"], "carry_env": ef["carry"],
            "status": sj.get("status", ""), "updated_at": int((sj.get("updatedAt") or 0) / 1000),
            "tmux_socket": "", "tmux_session": "", "attached": 0, "herdr_tab": "", "herdr_workspace": "",
        }
        if kind == "tmux":
            row["tmux_socket"], row["tmux_session"], row["attached"] = detail
            if row["status"] == "starting":
                # Has Claude Code stopped at its own "trust this folder?" question? Read the pane to find out.
                # Nothing from the pane is kept or printed.
                screen = tmux(detail[0], "capture-pane", "-p", "-t", f"={detail[1]}:", timeout=6)[1]
                if trust_dialog(screen):
                    row["status"] = "trust?"
        row["window"] = (kind in ("terminal", "ide", "ssh", "herdr")) or (kind == "tmux" and row["attached"] > 0)
        # a session with no terminal of any kind that the CLI did not start is some program's own: not ours to revive
        row["restorable"] = kind != "none" or row["entrypoint"] == "cli"
        if with_launch:
            row.update(launch_facts(pid, sj, ef["config_dir"]))
        mine.append(row)
    return mine, others, helpers


def running_sids(P=None):
    """every session id held by a live Claude of this user, in any terminal"""
    held = set()
    for pid, (_, uid, comm) in (P or procs()).items():
        if not is_claude(comm) or uid != os.getuid():
            continue
        sid = session_json(pid, env_facts(pid)["config_dir"]).get("sessionId")
        if sid:
            held.add(sid)
        m = re.search(r"--resume[ =]([0-9a-f-]{36})", " ".join(argv_of(pid)))
        if m:
            held.add(m.group(1))
    return held


def display(r):
    return r.get("name") or f"{os.path.basename(r.get('cwd') or '') or 'session'} ({r['session_id'][:8]})"


# ---------- herdr (optional) ----------
def herdr(*a, timeout=40):
    if not CFG["herdr"] or not CFG["herdr_tabs"]:
        return None
    rc, out = run([CFG["herdr"], *a], timeout=timeout)
    if rc != 0:
        return None
    try:
        return json.loads(out).get("result")
    except (ValueError, AttributeError):
        return None


def herdr_up():
    # never let a command start a Herdr server by itself
    return os.path.exists(os.path.join(HOME, ".config", "herdr", "herdr.sock")) and herdr("pane", "list") is not None


def clean_label(s):
    return re.sub(r"^[^\w(]+", "", s or "").strip()


def attach_of(argv):
    """(socket, tmux session) when argv is a `tmux attach`, else None"""
    if not argv or ("attach" not in argv and "attach-session" not in argv) or "-t" not in argv:
        return None
    sock = os.path.join(tmux_dir(), "default")
    if "-L" in argv and argv.index("-L") + 1 < len(argv):
        sock = os.path.join(tmux_dir(), argv[argv.index("-L") + 1])
    if "-S" in argv and argv.index("-S") + 1 < len(argv):
        sock = argv[argv.index("-S") + 1]
    if argv.index("-t") + 1 >= len(argv):
        return None
    return sock, argv[argv.index("-t") + 1].lstrip("=").split(":")[0]


def herdr_labels():
    """(socket, tmux session) -> (tab label, workspace label), and pid -> the same, from Herdr. Read only."""
    by_attach, by_pid = {}, {}
    pl, tl, wl = herdr("pane", "list"), herdr("tab", "list"), herdr("workspace", "list")
    if not pl or tl is None:
        return by_attach, by_pid
    tabs = {t["tab_id"]: clean_label(t.get("label")) for t in tl.get("tabs", [])}
    wss = {w["workspace_id"]: clean_label(w.get("label")) for w in (wl or {}).get("workspaces", [])}
    for p in pl.get("panes", []):
        lab = (tabs.get(p.get("tab_id"), ""), wss.get(p.get("workspace_id"), ""))
        info = herdr("pane", "process-info", "--pane", p["pane_id"]) or {}
        for pr in (info.get("process_info") or {}).get("foreground_processes") or []:
            if pr.get("pid"):
                by_pid[int(pr["pid"])] = lab
            a = attach_of(pr.get("argv"))
            if a and a not in by_attach:
                by_attach[a] = lab
    return by_attach, by_pid


# ---------- record ----------
def read_table():
    d = read_json(TABLE, {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("lanes", [])
    d.setdefault("down", [])
    return d


def record():
    force = "--force" in ARGS
    if not force:
        if in_boot_window() and not os.path.exists(marker()):
            log("record: skipped (the machine just started and this boot's revive has not finished)")
            return 0
        if CFG["auto_revive"] and not os.path.exists(marker()):
            log("record: skipped (this boot's revive has not run; the record is kept as it was)")
            return 0
    mine, _, _ = live()
    old = read_table()
    old_by = {r["session_id"]: r for r in old["lanes"]}
    labels = ({}, {})
    if "--no-herdr" not in ARGS and CFG["herdr"] and CFG["herdr_tabs"] and herdr_up():
        labels = herdr_labels()
    rows, used = [], set()
    skip = set(CFG.get("skip_tmux_sockets") or [])
    loose_skip = [p for p in (CFG.get("loose_skip") or []) if isinstance(p, str)]
    for r in mine:
        if not r["restorable"]:
            continue
        if r["host"] == "tmux" and os.path.basename(r["tmux_socket"]) in skip:
            continue           # another tool brings these back: one restorer per session
        if r["host"] != "tmux" and any(re.search(p, r["name"]) for p in loose_skip):
            continue           # a plain terminal the person told us to leave alone
        o = old_by.get(r["session_id"], {})
        tab, ws = (labels[0].get((r["tmux_socket"], r["tmux_session"])) or labels[1].get(r["pid"])
                   or (o.get("herdr_tab", ""), o.get("herdr_workspace", "")))
        if r["host"] == "herdr" and not tab:
            tab = r["name"]
        if tab and not ws:
            ws = CFG.get("herdr_default_workspace") or ""
        if r["host"] == "tmux":
            sock, name = r["tmux_socket"], r["tmux_session"]
        else:   # not in tmux today: it comes back in tmux (loose_socket), so it outlives its window
            sock = own_socket()
            name = str(CFG.get("loose_prefix") or "") + (slug(r["name"]) or f"claude-{r['session_id'][:8]}")
            if (sock, name) in used:
                name = f"{name}-{r['session_id'][:4]}"
        used.add((sock, name))
        rows.append({
            "name": r["name"], "session_id": r["session_id"], "cwd": r["cwd"], "model": r["model"],
            "effort": r["effort"], "mode": r["mode"], "add_dirs": r["add_dirs"], "host": r["host"],
            "tmux_socket": sock, "tmux_session": name, "herdr_tab": tab, "herdr_workspace": ws,
            "config_dir": r["config_dir"], "token_keys": r["token_keys"], "token_print": r["token_print"],
            "account": r["account"], "carry_env": r["carry_env"],
            "last_seen": int(time.time()),
        })
    if not rows and old["lanes"]:
        log(f"record: 0 live sessions; kept the previous record ({len(old['lanes'])} sessions)")
        return 0
    now, keep = int(time.time()), int(CFG["down_keep_days"]) * 86400
    live_ids = {r["session_id"] for r in rows}
    down = [d for d in old["down"] if d["session_id"] not in live_ids and now - d.get("down_since", now) < keep]
    known_down = {d["session_id"] for d in down}
    # Sessions that vanish together did not each end by a person's hand: that is a crash (a tmux server
    # that died, a terminal program that was closed with everything in it). They stay on the record as
    # sessions to bring back for lost_keep_h hours. `record --force` says: what runs now is the truth.
    # A recorded session whose own tmux session now holds another session was replaced there (a new
    # conversation in the same place, or a session that came back new): it is not down, it is over.
    taken = {(r["tmux_socket"], r["tmux_session"]) for r in rows}
    gone = [r for r in old["lanes"] if r["session_id"] not in live_ids and r["session_id"] not in known_down
            and (r["tmux_socket"], r["tmux_session"]) not in taken]
    fresh = [r for r in gone if not r.get("lost_since")]
    crash = not force and len(fresh) >= 3 and len(fresh) * 2 > len(old["lanes"])
    lost = []
    for r in gone:
        since = r.get("lost_since") or (now if crash else 0)
        if since and not force and now - since < float(CFG["lost_keep_h"]) * 3600:
            lost.append(dict(r, lost_since=since))
        else:
            down.append(dict({k: v for k, v in r.items() if k != "lost_since"}, down_since=now))
    if crash:
        log(f"record: {len(fresh)} of {len(old['lanes'])} recorded sessions vanished together (a crash?). They stay "
            f"on the record for {CFG['lost_keep_h']} h: `revive` brings them back; `record --force` lets them go.")
    rows += lost
    if DRY:
        for r in rows:
            log("record (dry): " + json.dumps(r, sort_keys=True))
        return 0
    if os.path.exists(TABLE):
        shutil.copyfile(TABLE, TABLE + ".prev")
    write_json(TABLE, {"version": 1, "recorded_at": now, "boot": boot_id(), "lanes": rows, "down": down})
    log(f"record: {len(rows)} session(s), {len(down)} down -> {TABLE}")
    write_tsv(rows)
    return 0


def write_tsv(rows):
    """Optional: the same record as a tab-separated table, for tools that read one. Eight columns."""
    path = os.path.expanduser(str(CFG.get("tsv_path") or ""))
    if not path:
        return
    only = str(CFG.get("tsv_socket") or "")

    def cell(v):
        return re.sub(r"[\t\r\n]+", " ", str(v or ""))

    lines = ["# written by Windstorm Tardigrade at each record; do not edit. " + "\t".join(COLS)]
    for r in rows:
        if only and os.path.basename(r["tmux_socket"]) != only:
            continue
        lines.append("\t".join(cell(x) for x in (
            r["tmux_session"], r["session_id"], r["cwd"], r.get("model"), r.get("effort"), r.get("mode"),
            r.get("herdr_tab"), r.get("herdr_workspace"))))
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path + ".new", "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(path + ".new", path)
    except OSError as e:
        log(f"record: could not write the table {path}: {e.strerror}")


# ---------- revive ----------
def flags_of(r):
    """The session's own flags, exactly. A permission mode is never made looser: what was recorded is
    what is passed, word for word."""
    flags = []
    if r["mode"] == "bypass":
        flags.append("--dangerously-skip-permissions")
    elif r["mode"] not in ("", "default"):
        flags += ["--permission-mode", r["mode"]]
    if r.get("model"):
        flags += ["--model", r["model"]]
    if r.get("effort"):
        flags += ["--effort", r["effort"]]
    for d in r.get("add_dirs") or []:
        flags += ["--add-dir", d]
    return flags


def fill(template, r):
    """Put a session's facts into a start line from the settings. Every value is quoted for the shell."""
    vals = {"session_id": r["session_id"], "cwd": r.get("cwd", ""), "name": r.get("name", ""),
            "model": r.get("model", ""), "effort": r.get("effort", ""), "mode": r.get("mode", ""),
            "account": r.get("account", ""), "tmux_session": r.get("tmux_session", "")}

    def sub(m):
        k = m.group(1)
        if k == "flags":
            return " ".join(shlex.quote(x) for x in flags_of(r))
        return shlex.quote(str(vals[k])) if k in vals else m.group(0)

    return re.sub(r"\{(\w+)\}", sub, str(template))


def claude_line(r):
    """the one command that starts this session again"""
    if CFG.get("start_command"):
        return fill(CFG["start_command"], r)
    if r.get("account") and CFG.get("account_command"):
        head = fill(CFG["account_command"], r)       # a session pinned to an account starts through its wrapper
    else:
        head = str(CFG.get("claude_command") or "claude")
    if r.get("fresh"):      # it never had a conversation, so there is none to resume: same name, folder and flags
        tail = ["--name", shlex.quote(r["name"])] if r.get("name") else []
    else:
        tail = ["--resume", r["session_id"]]
    return " ".join([head] + [shlex.quote(x) for x in flags_of(r)] + tail)


def env_file_pairs():
    """(pairs to set, names left out) from the env_file setting. A name that looks like a secret's is left out."""
    path = os.path.expanduser(str(CFG.get("env_file") or ""))
    if not path:
        return [], []
    try:
        with open(path, "rb") as f:
            raw = f.read().decode(errors="replace")
    except OSError:
        return [], ["(the file could not be read)"]
    nul = "\0" in raw
    keep, left = [], []
    for item in (raw.split("\0") if nul else raw.splitlines()):
        if not nul:
            item = item.strip()
            if item.startswith("export "):
                item = item[7:].lstrip()
        k, eq, v = item.partition("=")
        if not eq or item.startswith("#") or not NAME_OK.fullmatch(k):
            continue
        if not nul and len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        if k in NEVER_CARRY:
            continue
        if SECRET_NAME.search(k):
            left.append(k)
        else:
            keep.append((k, v))
    return keep, left


SAID = {}


def launch_dir():
    return os.path.join(STATE, "launch")


def launch_text(r):
    """what the start file for one session says"""
    sh = CFG["shell"]
    lines = ["# Windstorm Tardigrade start file. Written again at each revive; safe to delete."]
    pairs, left = env_file_pairs()
    if left and not SAID.get("env_file"):
        SAID["env_file"] = True      # once per run, names only
        log(f"  env_file: left out {', '.join(sorted(set(left)))} (a name that looks like a secret's is never copied)")
    lines += [f"export {k}={shlex.quote(v)}" for k, v in pairs]
    # A tmux server first started from inside a Claude session carries that session's environment.
    # CLAUDE_CODE_CHILD_SESSION turns transcript saving off, so none of it may reach a revived session.
    lines.append("unset " + CHILD_VARS)
    if r.get("config_dir"):
        lines.append(f"export CLAUDE_CONFIG_DIR={shlex.quote(r['config_dir'])}")
    ok = plain_names(CFG.get("carry_env"))
    for k, v in sorted((r.get("carry_env") or {}).items()):
        if k in ok and SAFE_VALUE.fullmatch(str(v)):
            lines.append(f"export {k}={shlex.quote(str(v))}")
    # the session's own folder, whatever the shell's start files or the env_file said
    lines.append(f"cd -- {shlex.quote(r['cwd'] if os.path.isdir(r['cwd']) else HOME)} 2>/dev/null")
    lines.append(f'{claude_line(r)}; echo; echo "[session ended; shell kept]"; exec {sh} -l')
    return "\n".join(lines) + "\n"


def write_launch_file(r):
    """The start file for one session, readable by this user only. The pane's command line is just "read
    this file", so no setting, no flag and no note sits on a command line, where a broad kill command that
    matches by text could match it."""
    os.makedirs(launch_dir(), mode=0o700, exist_ok=True)
    path = os.path.join(launch_dir(), r["session_id"] + ".sh")
    fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(launch_text(r))
    os.replace(path + ".new", path)
    return path


def launch_cmd(r):
    sh = CFG["shell"]
    return f"{sh} -{'l' if sh == 'zsh' else ''}ic {shlex.quote('. ' + shlex.quote(write_launch_file(r)))}"


def pace():
    """Optional (load_max): on a loaded machine, wait a little before the next start."""
    mx = float(CFG.get("load_max") or 0)
    if mx <= 0 or load_now() <= mx:
        return
    end = time.time() + float(CFG["load_wait_s"])
    log(f"  load is {load_now():.0f}, above load_max {mx:.0f}: waiting up to {int(float(CFG['load_wait_s']))} s "
        "before the next start")
    while load_now() > mx and time.time() < end:
        time.sleep(1 if TESTING else 5)


def server_up(sock):
    return os.path.exists(sock) and tmux(sock, "list-sessions", timeout=6)[0] == 0


def server_env():
    """the environment a tmux server is started with: clean, plus the env_file (a desktop's display and bus)"""
    e = clean_env()
    e.update({k: v for k, v in env_file_pairs()[0]})
    return e


def ensure_server(sock):
    """With server_command set: when no tmux server is up on this socket, run that command first, once.
    A machine can need its server started its own way (its own config file, its own cgroup, a keeper
    session). Without the setting, or if the command leaves no server, tmux starts one at the first session."""
    name = os.path.basename(sock)
    cmd, only = str(CFG.get("server_command") or ""), str(CFG.get("server_socket") or "")
    if not cmd or (only and only != name) or name in SAID.setdefault("server", set()) or server_up(sock):
        return
    SAID["server"].add(name)
    line = cmd.replace("{socket_name}", shlex.quote(name)).replace("{socket}", shlex.quote(sock))
    rc = run(["/bin/sh", "-c", line], timeout=90, env=server_env())[0]
    end = time.time() + 20
    while not server_up(sock) and time.time() < end:
        time.sleep(1)
    log(f"  tmux server '{name}': your server_command ran (exit {rc}); the server is "
        + ("up" if server_up(sock) else "NOT up, so tmux starts one itself at the first session"))


def clean_env():
    user = os.environ.get("USER") or os.path.basename(HOME)
    e = {"HOME": HOME, "USER": user, "LOGNAME": user, "SHELL": shutil.which(CFG["shell"]) or f"/bin/{CFG['shell']}",
         "LANG": os.environ.get("LANG") or "en_US.UTF-8", "TERM": "xterm-256color", "PATH": CFG["path"]}
    for k in ("TMPDIR", "XDG_RUNTIME_DIR", "TARDIGRADE_TESTING"):
        if os.environ.get(k):
            e[k] = os.environ[k]
    return e


def login_print(r):
    """the recorded login fingerprint: "" for none, "unknown" for a record made before fingerprints"""
    return r.get("token_print") or ("unknown" if r.get("token_keys") else "")


def same_login(r, pid):
    want = login_print(r)
    return want == "unknown" or env_facts(pid)["token_print"] == want


def start_lane(r, created):
    sock, name = r["tmux_socket"], r["tmux_session"]
    cmd = launch_cmd(r)
    cwd = r["cwd"] if os.path.isdir(r["cwd"]) else HOME
    if (sock, name) in created and has_session(sock, name):
        # a retry: the session this run made holds a shell because claude ended
        cur = tmux(sock, "display", "-p", "-t", f"={name}:", "#{pane_current_command}")[1].strip()
        if cur not in SHELLS:
            log(f"  {display(r)}: its tmux session is busy ({cur}); not touching it")
            return False
        rc = tmux(sock, "respawn-pane", "-k", "-t", f"={name}:", "-c", cwd, cmd)[0]
    else:
        os.makedirs(os.path.dirname(sock), mode=0o700, exist_ok=True)
        ensure_server(sock)
        argv = [CFG["tmux"], "-S", sock]
        conf = os.path.expanduser(str(CFG.get("tmux_conf") or ""))
        if conf and os.path.isfile(conf):
            argv += ["-f", conf]         # read only when this call is the one that starts the server
        argv += ["new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", cwd, cmd]
        rc = run(argv, env=server_env())[0]      # nothing of the caller's environment, and no value on a command line
        created.add((sock, name))
    return rc == 0


def came_up(r, P=None):
    """The Claude process in this session's tmux pane, or None. P: a process list taken a moment ago; on a
    machine with thousands of processes one list per pass is taken and handed down, not one per session."""
    rc, out = tmux(r["tmux_socket"], "list-panes", "-t", f"={r['tmux_session']}", "-F", "#{pane_pid}")
    if rc != 0 or not out.split():
        return None
    c = claude_under(int(out.split()[0]), P or procs())
    return c if c and alive(c) else None


YES_LINE = re.compile(r"❯\s*(\d\.\s*)?Yes, I trust")
OTHER_DIALOG = re.compile(r"usage credits|usage limit|limit reached|Stop and wait|Wait here|Switch to", re.I)


def bottom(screen, n):
    return "\n".join([ln for ln in screen.splitlines() if ln.strip()][-n:])


def trust_dialog(screen):
    """The foot of the screen if Claude Code's folder question is the dialog open there now, else "".
    A resumed session redraws its old conversation, and a conversation can quote this very dialog, cursor
    mark and all. So nothing higher up counts: both choices must be in the last 6 lines that have text,
    and the 15 lines above the foot must say nothing about usage or limits. If another dialog is open
    (the one that offers usage credits, say), a key meant for this one would land in that one."""
    foot, near = bottom(screen, 6), bottom(screen, 15)
    if "No, exit" not in foot or "Yes, I trust" not in foot or "trust this folder" not in near:
        return ""
    if OTHER_DIALOG.search(near):
        return ""
    return foot


def answer_trust(r):
    """Only when the person has switched it on (`trust on`), and only for a session this run started, in
    the folder it was recorded in: if Claude Code's "trust this folder?" question is the dialog open at
    the foot of its pane, choose Yes. The screen is read again right before the key that confirms, and
    Enter is sent only with the cursor on Yes."""
    sock, target = r["tmux_socket"], f"={r['tmux_session']}:"

    def look():
        return trust_dialog(tmux(sock, "capture-pane", "-p", "-t", target, timeout=6)[1])

    foot = look()
    if not foot:
        return False
    if not YES_LINE.search(foot):
        tmux(sock, "send-keys", "-t", target, "Down")
        time.sleep(1)
    foot = look()                       # looked at again, right before the key that confirms
    if not foot or not YES_LINE.search(foot):
        log(f"  {display(r)}: the trust question is open, but the cursor is not on Yes; left for you")
        return False
    tmux(sock, "send-keys", "-t", target, "Enter")
    log(f"  {display(r)}: answered Yes to \"trust this folder?\" for its own folder {r['cwd']} (you switched this on)")
    return True


QUESTION = re.compile(r"trust this folder|No, exit|❯\s*\d\.\s|usage credits|Stop and wait")


def type_note(r, note):
    """The wake-up note is typed, never put on a command line. Called only for a session this run started,
    whose login was checked, once Claude Code reports it idle and its screen shows no question."""
    sock, target = r["tmux_socket"], f"={r['tmux_session']}:"
    tmux(sock, "send-keys", "-t", target, "-l", note)
    time.sleep(0.5)
    tmux(sock, "send-keys", "-t", target, "Enter")


class Watch:
    """Looks at the sessions this run started until each has loaded (and has its note, where one is due).
    A slow machine can take minutes to load a session or to draw a question, so one look right after the
    start is not enough. It works in passes, so that sessions which are up are looked after while another
    start is still being retried. At the end it counts: LOADED means Claude Code has written the session's
    own file, not merely that a process is up."""

    def __init__(self, note_for, tab_rows):
        self.note_for, self.tab_rows = note_for, tab_rows
        self.answered, self.noted, self.noted_ids, self.settled, self.gone = [], [], set(), set(), set()
        self.tabs_due = bool(CFG["herdr"] and CFG["herdr_tabs"] and any(r.get("herdr_tab") for r in tab_rows))

    def look(self, started):
        """one pass over the started sessions; returns how many are still waited for"""
        if self.tabs_due and herdr_up():
            attach_tabs(self.tab_rows, wait=False)
            self.tabs_due = False
        waiting, P = 0, procs()               # one process list per pass, for every session in it
        for r in started:
            sid = r["session_id"]
            if sid in self.settled or sid in self.gone:
                continue
            pid = came_up(r, P)
            if not pid:
                log(f"  {display(r)}: went down again while being watched")
                self.gone.add(sid)
                continue
            sj = session_json(pid, r.get("config_dir", ""))
            if not sj.get("sessionId"):
                if CFG["answer_trust"] and answer_trust(r):
                    self.answered.append(display(r))
                waiting += 1
                continue
            if sid in self.note_for and sid not in self.noted_ids:
                screen = tmux(r["tmux_socket"], "capture-pane", "-p", "-t", f"={r['tmux_session']}:", timeout=6)[1]
                if sj.get("status") == "idle" and not QUESTION.search(bottom(screen, 15)):
                    type_note(r, self.note_for[sid])
                    self.noted.append(display(r))
                    self.noted_ids.add(sid)
                    log(f"  {display(r)}: loaded and idle; wake-up note typed")
                else:
                    waiting += 1
                    continue
            self.settled.add(sid)
        return waiting

    def count(self, started):
        res = {"trust_answered": self.answered, "noted": self.noted, "loaded": [], "not_loaded": [],
               "down_again": [], "note_not_typed": []}
        P = procs()
        for r in started:
            pid = came_up(r, P)
            if not pid:
                res["down_again"].append(display(r))
            elif not session_json(pid, r.get("config_dir", "")).get("sessionId"):
                res["not_loaded"].append(display(r))
            else:
                res["loaded"].append(display(r))
                if r["session_id"] in self.note_for and r["session_id"] not in self.noted_ids:
                    res["note_not_typed"].append(display(r))
        log(f"count: started={len(started)} LOADED={len(res['loaded'])} UP-NOT-LOADED={len(res['not_loaded'])} "
            f"DOWN={len(res['down_again'])}")
        for nm in res["not_loaded"]:
            log(f"  UP-NOT-LOADED {nm}: Claude Code runs but has not loaded the session (a question on its screen?)")
        for nm in res["down_again"]:
            log(f"  DOWN {nm}: it started and ended again; its tmux session holds a shell")
        for nm in res["note_not_typed"]:
            log(f"  {nm}: loaded, but not idle (a question on its screen?); the wake-up note was not typed")
        if self.answered:
            log(f"trust: answered for {len(self.answered)} session(s)")
        return res


def revive():
    os.makedirs(STATE, exist_ok=True)
    boot = "--boot" in ARGS
    if boot and not DRY:
        if not in_boot_window():
            log("revive --boot: the machine has been up too long to be a boot; nothing done")
            return 0
        if os.path.exists(marker()):
            log("revive --boot: this boot's revive already ran; nothing done")
            return 0
    lock = os.path.join(STATE, "lock")
    owner = os.path.join(lock, "owner")
    try:
        os.mkdir(lock)
    except FileExistsError:
        # A revive that was cut off by a reboot or a power cut leaves its lock behind. The lock says which
        # boot and which process took it: a lock from another boot, or from a process that is gone, is stale.
        try:
            with open(owner) as f:
                o_boot, o_pid = f.read().split()
            stale = o_boot != boot_id() or not alive(int(o_pid))
        except (OSError, ValueError):
            # no owner written (a lock from version 0.1.6 or older): stale if it is older than this boot
            made = os.path.getmtime(lock)
            stale = made < time.time() - uptime_s() or time.time() - made >= 7200
        if not stale:
            log("revive: another revive is running (lock); nothing done")
            return 0
        log("revive: a lock was left by a revive that did not finish (a reboot or a power cut during it); taking over")
        shutil.rmtree(lock, ignore_errors=True)
        os.mkdir(lock)
    with open(owner, "w") as f:
        f.write(f"{boot_id()} {os.getpid()}\n")
    try:
        return _revive(boot)
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def _revive(boot):
    if not CFG["tmux"]:
        log("revive: tmux is not installed; nothing can be started. Install tmux, then run setup again.")
        return 1
    rows = read_table()["lanes"]
    if "--only" in ARGS and ARGS.index("--only") + 1 < len(ARGS):
        want = ARGS[ARGS.index("--only") + 1]
        rows = [r for r in rows if want in (r["name"], r["session_id"], r["tmux_session"])]
        if not rows:
            log(f"revive --only: no session on the record is named {want!r}")
            return 1
    log(f"=== revive v{VERSION} (boot={boot}, dry-run={DRY}) uptime={int(uptime_s())} s, {len(rows)} to look at")
    held = running_sids()
    note = WAKE_NOTE.format(when=time.strftime("%Y-%m-%d %H:%M %Z")) if CFG["wake"] == "note" else ""
    todo, report = [], {"started": [], "skipped": [], "failed": [], "needs_you": []}
    # Nothing is typed into a session before its login is checked: a session that comes back on another
    # login than it had (possible where sessions on one machine are pinned to accounts) is listed as
    # "needs you" and gets no note, so nothing runs on the wrong account.
    as_if_down = DRY and "--all" in ARGS      # show every start line, as if nothing ran: for checking before a reboot
    for r in rows:
        nm = display(r)
        if r["session_id"] in held and not as_if_down:
            log(f"skip  {nm}: already running on this machine")
            report["skipped"].append(nm)
            continue
        if has_session(r["tmux_socket"], r["tmux_session"]) and not as_if_down:
            log(f"skip  {nm}: a tmux session named {r['tmux_session']} already exists")
            report["skipped"].append(nm)
            continue
        log(f"start {nm} ({r['session_id'][:8]}, {r['mode']}, {r.get('model') or 'default model'}) in {r['cwd']}"
            + (f"  [account {r['account']}]" if r.get("account") else "")
            + ("  [pinned login: checked before anything is typed]" if login_print(r) else ""))
        if DRY:
            log(f"      tmux {os.path.basename(r['tmux_socket'])}:{r['tmux_session']}   {claude_line(r)}")
            if "--start-file" in ARGS:      # to the screen only, never to the log: it holds the env_file's values
                print(launch_text(r), end="")
        todo.append(r)
    if DRY:
        return 0
    try:        # start files of sessions that are no longer on the record
        on_record = {r["session_id"] + ".sh" for r in read_table()["lanes"]}
        for f in os.listdir(launch_dir()):
            if f not in on_record:
                os.remove(os.path.join(launch_dir(), f))
    except OSError:
        pass
    created, came, note_for = set(), [], {}
    pending = {r["session_id"]: [r, 0] for r in todo}
    deadline = time.time() + float(CFG["retry_min"]) * 60
    watch_s = (float(CFG["watch_min"]) if boot else min(float(CFG["watch_min"]), 3)) * 60
    w = Watch(note_for, rows)
    next_try, last_up, tries_done, said = 0.0, time.time(), False, False

    def try_start(r):
        """one start; the process id of its Claude if it came up"""
        if not start_lane(r, created):
            return None
        time.sleep(float(CFG["settle_s"]))
        return came_up(r)

    while True:
        if pending and not tries_done and time.time() >= next_try:
            for sid, (r, n) in list(pending.items()):
                nm = display(r)
                P = procs()            # taken fresh for each session, right before the decision to start it
                late = came_up(r, P) if n else None
                if late:
                    log(f"  {nm}: up (came up late)")
                    report["started"].append(nm)
                    came.append(r)
                    last_up = time.time()
                    if note and same_login(r, late) and not r.get("fresh"):
                        note_for[sid] = note
                    del pending[sid]
                    continue
                if sid in running_sids(P):
                    log(f"  {nm}: something else started it meanwhile; left alone")
                    report["skipped"].append(nm)
                    del pending[sid]
                    continue
                pace()
                pid = try_start(r)
                if not pid and not r.get("fresh") and not CFG.get("start_command") and "No conversation found" in tmux(
                        r["tmux_socket"], "capture-pane", "-p", "-t", f"={r['tmux_session']}:", timeout=6)[1]:
                    # Claude Code keeps no conversation for a session nobody ever wrote in. There is nothing to
                    # resume and nothing to lose: it comes back as a new session with the same name, folder and flags.
                    log(f"  {nm}: Claude Code has no conversation for it (nobody ever wrote in it). "
                        "Starting it new, with the same name, folder and flags.")
                    r["fresh"] = True
                    report.setdefault("fresh", []).append(nm)
                    pid = try_start(r)
                if pid:
                    came.append(r)
                    last_up = time.time()
                    child = ("CLAUDE_CODE_CHILD_SESSION PRESENT (BAD: this session does not save its transcript)"
                             if env_facts(pid)["child"] else "child marker absent")
                    if same_login(r, pid):
                        log(f"  {nm}: up ({child}" + ("; same login as before)" if login_print(r) else ")"))
                        report["started"].append(nm)
                        if note and not r.get("fresh"):
                            note_for[sid] = note
                    else:
                        log(f"needs you  {nm}: it came back on a different login than it had before. "
                            "It is idle and nothing was typed. Close it and start it your usual way.")
                        report["needs_you"].append(nm)
                    del pending[sid]
                    time.sleep(float(CFG["stagger_s"]))
                    continue
                pending[sid][1] += 1
                log(f"  {nm}: not running yet (attempt {pending[sid][1]}); will retry")
                time.sleep(float(CFG["stagger_s"]))
            next_try = time.time() + (1 if TESTING else 60)
        if not tries_done and (not pending or time.time() >= deadline):
            tries_done = True
            for sid, (r, n) in pending.items():
                log(f"  {display(r)}: GAVE UP after {n} attempt(s); its tmux session holds a shell")
                report["failed"].append(display(r))
            report.update(at=int(time.time()), boot=boot_id(), on_record=len(rows))
            write_json(os.path.join(STATE, "last-revive.json"), report)
            if boot:
                with open(marker(), "w") as f:
                    f.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
            log(f"revive: {len(report['started'])} started, {len(report['skipped'])} already running, "
                f"{len(report['failed'])} failed, {len(report['needs_you'])} need you")
        # the sessions that are up are looked after in every pass, also while another start is being retried
        waiting = w.look(came) if came else 0
        if tries_done and (not waiting or time.time() >= last_up + watch_s):
            break
        if waiting and not said:
            log(f"watching {waiting} session(s) until loaded, for up to {watch_s / 60:g} min")
            said = True
        time.sleep(1 if TESTING else 10)
    if came:
        report.update(w.count(came))
        write_json(os.path.join(STATE, "last-revive.json"), report)
    notify(f"{len(report['started'])} session(s) brought back, {len(report['failed']) + len(report['needs_you'])} need you")
    if w.tabs_due:
        attach_tabs(rows)
    return 0


def notify(text):
    """a desktop note, when there is a desktop; never an error"""
    try:
        if MAC:
            run(["osascript", "-e", f'display notification "{text}" with title "Windstorm Tardigrade"'], timeout=5)
        elif shutil.which("notify-send") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            run(["notify-send", "Windstorm Tardigrade", text], timeout=5)
    except Exception:
        pass


def attach_tabs(rows, wait=True):
    """Herdr only: put each revived session back in the tab that carries its label. The one thing typed
    into a Herdr pane is the `tmux attach` for that session, and only when the pane holds an idle shell
    with nothing typed after its prompt."""
    want = [r for r in rows if r.get("herdr_tab")]
    if not want or not CFG["herdr"] or not CFG["herdr_tabs"]:
        return
    end = time.time() + (float(CFG["attach_wait_min"]) * 60 if wait else 0)
    while not herdr_up():
        if time.time() > end:
            log("herdr: its server never came up; tabs left as they are")
            return
        time.sleep(30)
    pl, tl, wl = herdr("pane", "list"), herdr("tab", "list") or {}, herdr("workspace", "list") or {}
    tabs, pns = tl.get("tabs", []), (pl or {}).get("panes", [])
    for r in want:
        sock, s = r["tmux_socket"], r["tmux_session"]
        if not has_session(sock, s):
            continue
        att = f"env -u TMUX {shlex.quote(CFG['tmux'])} -S {shlex.quote(sock)} attach -t {shlex.quote('=' + s)}"
        cand = [t for t in tabs if clean_label(t.get("label")) == r["herdr_tab"]]
        ws = [w for w in wl.get("workspaces", []) if clean_label(w.get("label")) == r.get("herdr_workspace")]
        if ws:
            cand = [t for t in cand if t.get("workspace_id") == ws[0]["workspace_id"]] or cand
        if not cand:
            if not ws:
                log(f"herdr {display(r)}: no tab '{r['herdr_tab']}' and no workspace '{r.get('herdr_workspace')}'; "
                    "it runs in tmux with no tab")
                continue
            res = herdr("tab", "create", "--workspace", ws[0]["workspace_id"], "--label", r["herdr_tab"])
            pane = ((res or {}).get("root_pane") or {}).get("pane_id")
            if pane:
                time.sleep(2)
                herdr("pane", "run", pane, att)
                log(f"herdr {display(r)}: new tab '{r['herdr_tab']}'")
            continue
        tp = [p for p in pns if p.get("tab_id") == cand[0]["tab_id"]]
        if not tp:
            continue
        pid = tp[0]["pane_id"]
        pi = (herdr("pane", "process-info", "--pane", pid) or {}).get("process_info") or {}
        fg = pi.get("foreground_processes") or []
        if any(attach_of(p.get("argv")) == (sock, s) for p in fg):
            continue
        if not (len(fg) == 1 and fg[0].get("pid") == pi.get("shell_pid")):
            log(f"herdr {display(r)}: its tab is running something else; left alone")
            continue
        rc, out = run([CFG["herdr"], "pane", "read", pid, "--source", "visible"], timeout=40)
        last = [ln for ln in out.splitlines() if ln.strip()][-1:] or [""]
        if not re.search(r"[$%#❯]\s*$", last[0]):
            log(f"herdr {display(r)}: its tab has text typed after the prompt; left alone")
            continue
        herdr("pane", "run", pid, att)
        log(f"herdr {display(r)}: tab '{r['herdr_tab']}' re-attached")


# ---------- census ----------
def census_data():
    mine, others, helpers = live(with_launch=False)
    table = read_table()
    live_ids = {r["session_id"] for r in mine}
    down = [r for r in table["lanes"] if r["session_id"] not in live_ids]
    down += [r for r in table["down"] if r["session_id"] not in live_ids]
    hidden = [r for r in mine if not r["window"]]
    shown = [r for r in mine if r["window"]]
    return {
        "at": int(time.time()), "uptime_s": int(uptime_s()), "running": len(mine), "with_window": len(shown),
        "hidden": len(hidden), "down": len(down), "other_users": others, "helpers": helpers,
        "waiting": len([r for r in mine if r["status"] in ("waiting", "trust?")]),
        "trust": len([r for r in mine if r["status"] == "trust?"]),
        "lost": len([r for r in down if r.get("lost_since")]), "comes_back": comes_back(),
        "auto_revive": bool(CFG["auto_revive"]), "recorded_at": table.get("recorded_at", 0),
        "on_record": len(table["lanes"]), "last_revive": read_json(os.path.join(STATE, "last-revive.json"), {}),
        "sessions": {"hidden": hidden, "with_window": shown, "down": down},
    }


def where_text(r):
    if r["host"] == "tmux":
        return f"tmux {os.path.basename(r['tmux_socket'])}:{r['tmux_session']}"
    return {"herdr": "Herdr", "terminal": "a terminal window", "ide": "an editor", "ssh": "an ssh login",
            "none": "no terminal at all"}.get(r["host"], r["host"])


def census():
    d = census_data()
    if "--json" in ARGS:
        print(json.dumps(d, indent=1, sort_keys=True))
        return 0
    if "--save" in ARGS:
        p = os.path.join(STATE, f"census-{time.strftime('%Y%m%dT%H%M%S')}.json")
        write_json(p, d)
        print(f"saved {p}")
    now = time.time()
    print(f"Windstorm Tardigrade census, {time.strftime('%Y-%m-%d %H:%M %Z')} (machine up {ago(d['uptime_s'])})")
    print(f"Running: {d['running']}   with a window: {d['with_window']}   "
          f"hidden (no window open): {d['hidden']}   down: {d['down']}")
    if d["waiting"]:
        print(f"Waiting for you: {d['waiting']} (a question or a dialog is open in the session)")
    rec = f"{ago(now - d['recorded_at'])} ago, {d['on_record']} session(s)" if d["recorded_at"] else "never"
    print(f"Comes back after a reboot: {d['comes_back']}   Last record: {rec}")
    lr = d["last_revive"]
    if lr:
        print(f"Last revive: {time.strftime('%Y-%m-%d %H:%M', time.localtime(lr.get('at', 0)))}: "
              f"{len(lr.get('started', []))} started, {len(lr.get('failed', []))} failed, "
              f"{len(lr.get('needs_you', []))} need you")

    def table(title, rows, down=False):
        if not rows:
            return
        print(f"\n{title}")
        for r in sorted(rows, key=lambda x: display(x).lower()):
            if down:
                t = r.get("down_since") or r.get("last_seen") or 0
                print(f"  {display(r):<34} last seen {ago(now - t)} ago   {r.get('cwd', '')}")
            else:
                q = ago(now - r["updated_at"]) if r["updated_at"] else "?"
                print(f"  {display(r):<34} {r['status'] or '?':<8} quiet {q:<8} {where_text(r):<30} {r['cwd']}")

    table("HIDDEN: running, but no window shows them", d["sessions"]["hidden"])
    table("WITH A WINDOW", d["sessions"]["with_window"])
    table("DOWN: on the record, not running now", d["sessions"]["down"], down=True)
    if d["lost"]:
        print(f"\n{d['lost']} of the sessions under DOWN vanished together, which looks like a crash. They are still "
              f"on the record: `tardigrade revive` brings them back. If you ended them yourself: record --force")
    if d["other_users"]:
        print(f"\nOther users on this machine: {d['other_users']} Claude process(es). Not yours; not read.")
    if d["helpers"]:
        print(f"Helper sessions started by other sessions: {d['helpers']} (not brought back after a reboot).")
    if d["trust"]:
        print(f"\ntrust? = Claude Code asks whether to trust that session's folder, and the session has not loaded yet "
              f"({d['trust']}). A session in your home folder is asked on every start. Open its window and answer, "
              f"or have it answered after a restart: tardigrade trust on")
    if d["hidden"]:
        print("\nTo open a window for each hidden one: tardigrade open")
    return 0


# ---------- open windows ----------
def open_windows():
    mine, _, _ = live(with_launch=False)
    hidden = [r for r in mine if not r["window"] and r["host"] == "tmux"]
    if not hidden:
        print("No hidden session in tmux: nothing to open.")
        return 0
    desk = MAC or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    term = None
    if not MAC:
        for t in [os.environ.get("TERMINAL"), "x-terminal-emulator", "gnome-terminal", "konsole", "kitty", "alacritty", "xterm"]:
            if t and shutil.which(t):
                term = t
                break
    for r in hidden:
        att = [CFG["tmux"], "-S", r["tmux_socket"], "attach", "-t", "=" + r["tmux_session"]]
        line = " ".join(shlex.quote(x) for x in att)
        if DRY or not desk or (not MAC and not term):
            print(f"{display(r)}: {line}")
            continue
        if MAC:
            run(["osascript", "-e", f'tell application "Terminal" to do script "{line}"'], timeout=15)
        else:
            base = os.path.basename(term)
            if base == "gnome-terminal":
                argv = [term, "--title", display(r), "--"] + att
            elif base in ("xterm", "x-terminal-emulator"):
                argv = [term, "-T", display(r), "-e"] + att
            else:
                argv = [term, "-e"] + att
            subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"opened a window for {display(r)}")
        time.sleep(0.5)
    if not desk:
        print("No desktop here, so no window was opened. Type a line above in a terminal to see that session.")
    return 0


# ---------- setup ----------
def units(py, script):
    revive_unit = f"""[Unit]
Description=Windstorm Tardigrade: bring Claude Code sessions back after boot, never a second copy

[Service]
Type=oneshot
RemainAfterExit=yes
# the tmux server it starts must outlive this one-shot service
KillMode=process
TimeoutStartSec=0
Nice=5
ExecStartPre=/bin/sleep 45
ExecStart={py} -I {script} revive --boot

[Install]
WantedBy=default.target
"""
    record_unit = f"""[Unit]
Description=Windstorm Tardigrade: write down the live Claude Code sessions (reads only)

[Service]
Type=oneshot
Nice=10
ExecStart={py} -I {script} record
"""
    timer = """[Unit]
Description=Windstorm Tardigrade: record the live sessions every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
"""
    return {"windstorm-tardigrade-revive.service": revive_unit, "windstorm-tardigrade-record.service": record_unit,
            "windstorm-tardigrade-record.timer": timer}


def plist(label, py, script, verb, extra):
    out = os.path.join(STATE, label + ".out")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string><string>{verb} {py} -I {script}</string></array>
{extra}
<key>AbandonProcessGroup</key><true/>
<key>StandardOutPath</key><string>{out}</string>
<key>StandardErrorPath</key><string>{out}</string>
</dict></plist>
"""


def user_env():
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return dict(os.environ, XDG_RUNTIME_DIR=rt,
                DBUS_SESSION_BUS_ADDRESS=os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={rt}/bus")


def setup():
    py = sys.executable or shutil.which("python3") or "/usr/bin/python3"
    script = os.path.join(INSTALL_DIR, "tardigrade.py")
    if sys.platform.startswith("win"):
        print("Windows is not supported yet. Under WSL, run setup inside the Linux system.")
        return 1
    if "--print-units" in ARGS:
        for name, text in units(py, script).items():
            print(f"### {name}\n{text}")
        return 0
    if not CFG["tmux"]:
        print("tmux is not installed. Tardigrade starts sessions inside tmux, so they outlive their windows.\n"
              "Install it (apt install tmux, dnf install tmux or brew install tmux), then run setup again.")
        return 1
    if DRY:
        print(f"would copy this script to {script}, install the boot service and the 5-minute record timer, and record once")
        return 0
    os.makedirs(INSTALL_DIR, exist_ok=True)
    os.makedirs(STATE, exist_ok=True)
    if os.path.abspath(__file__) != script:
        shutil.copyfile(os.path.abspath(__file__), script)
    if MAC:
        la = os.path.join(HOME, "Library", "LaunchAgents")
        os.makedirs(la, exist_ok=True)
        uid = str(os.getuid())
        for label, verb, extra in (
                ("ai.thewindstorm.tardigrade-revive", "sleep 45; exec", "<key>RunAtLoad</key><true/>"),
                ("ai.thewindstorm.tardigrade-record", "exec", "<key>StartInterval</key><integer>300</integer>")):
            p = os.path.join(la, label + ".plist")
            arg = " revive --boot" if "revive" in label else " record"
            with open(p, "w") as f:
                f.write(plist(label, py, script + arg, verb, extra))
            run(["launchctl", "bootout", f"gui/{uid}/{label}"])
            if run(["launchctl", "bootstrap", f"gui/{uid}", p])[0] != 0:
                print(f"NOT set up: launchd did not take {label}. Nothing comes back by itself yet.")
                return 1
    else:
        ud = os.path.join(HOME, ".config", "systemd", "user")
        os.makedirs(ud, exist_ok=True)
        for name, text in units(py, script).items():
            with open(os.path.join(ud, name), "w") as f:
                f.write(text)
        env = user_env()
        run(["systemctl", "--user", "daemon-reload"], env=env)
        run(["systemctl", "--user", "enable", "windstorm-tardigrade-revive.service"], env=env)
        run(["systemctl", "--user", "enable", "--now", "windstorm-tardigrade-record.timer"], env=env)
        svc = run(["systemctl", "--user", "is-enabled", "windstorm-tardigrade-revive.service"], env=env)[1].strip()
        tim = run(["systemctl", "--user", "is-active", "windstorm-tardigrade-record.timer"], env=env)[1].strip()
        print(f"boot service: {svc or 'no answer'}   record timer: {tim or 'no answer'}")
        if svc != "enabled" or tim != "active":
            print("NOT set up: systemd's user services did not take the units. The files are written in "
                  "~/.config/systemd/user/. Log out and in, then run setup again. Nothing comes back by itself yet.")
            return 1
    save_cfg_key("auto_revive", True)
    # this boot counts as revived: the sessions running now are the ones to remember
    with open(marker(), "w") as f:
        f.write("setup " + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
    ARGS.append("--force")
    record()
    print("\nWindstorm Tardigrade is set up. After a restart, your sessions come back by themselves.")
    for line in who_logs_in():
        print(line)
    return 0


def mac_story(filevault, auto_user, me):
    """(a short phrase for the census, the full lines for setup) from what the Mac says about itself"""
    agent = ("On a Mac the boot agent runs only when YOU are logged in at its screen. A login over ssh does "
             "not start it, and neither does another user's login.")
    if "FileVault is On" in filevault:
        return ("only after a person unlocks this Mac (FileVault) and you are logged in at its screen",
                ["FileVault is on: after a restart this Mac waits at its unlock screen, and NOTHING comes back "
                 "until a person types the password there.", agent])
    if auto_user and auto_user == me:
        return ("yes, by itself (this Mac logs you in)",
                ["This Mac logs you in by itself after a restart, so sessions come back with nobody there.", agent])
    if auto_user:
        return ("only after YOU log in at its screen (this Mac logs in as another user by itself)",
                ["This Mac logs in by itself after a restart, but as another user, not as you. Your sessions "
                 "come back only when you log in at its screen.", agent])
    return ("after you log in at its screen",
            ["After a restart, sessions come back when you log in at this Mac's screen.", agent])


def linger_on():
    if TESTING and os.environ.get("TARDIGRADE_FAKE_LINGER"):
        return os.environ["TARDIGRADE_FAKE_LINGER"] == "yes"
    user = os.environ.get("USER") or os.path.basename(HOME)
    return run(["loginctl", "show-user", user, "-p", "Linger", "--value"], timeout=10)[1].strip() == "yes"


def mac_facts():
    fv = run(["fdesetup", "status"], timeout=10)[1]
    auto = run(["defaults", "read", "/Library/Preferences/com.apple.loginwindow", "autoLoginUser"], timeout=10)
    return fv, (auto[1].strip() if auto[0] == 0 else ""), os.environ.get("USER") or os.path.basename(HOME)


def comes_back():
    """the census's short answer to: after a reboot, do the sessions come back?"""
    if not CFG["auto_revive"]:
        return "NO (run setup)"
    if MAC:
        return mac_story(*mac_facts())[0]
    return "yes, by itself" if linger_on() else "yes, after you log in (lingering is off)"


def who_logs_in():
    """Say plainly what must happen after a restart before sessions can come back."""
    out = []
    if MAC:
        out += mac_story(*mac_facts())[1]
    else:
        user = os.environ.get("USER") or os.path.basename(HOME)
        linger = "yes" if linger_on() else "no"
        if linger == "yes":
            out.append("Lingering is on: sessions come back about a minute after boot, with nobody logged in.")
        else:
            out.append("Lingering is OFF: sessions come back only after you log in to this machine.")
            out.append(f"To bring them back with nobody logged in, a person runs once: sudo loginctl enable-linger {user}")
    return out


def uninstall():
    if DRY:
        print("would remove the boot service, the record timer and the installed copy; the record and the log stay")
        return 0
    if MAC:
        for label in ("ai.thewindstorm.tardigrade-revive", "ai.thewindstorm.tardigrade-record"):
            run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"])
            try:
                os.remove(os.path.join(HOME, "Library", "LaunchAgents", label + ".plist"))
            except OSError:
                pass
    else:
        env = user_env()
        run(["systemctl", "--user", "disable", "--now", "windstorm-tardigrade-record.timer"], env=env)
        run(["systemctl", "--user", "disable", "windstorm-tardigrade-revive.service"], env=env)
        for name in units("", ""):
            try:
                os.remove(os.path.join(HOME, ".config", "systemd", "user", name))
            except OSError:
                pass
        run(["systemctl", "--user", "daemon-reload"], env=env)
    try:
        os.remove(os.path.join(INSTALL_DIR, "tardigrade.py"))
    except OSError:
        pass
    save_cfg_key("auto_revive", False)
    print("Removed. Nothing comes back by itself any more. Your record and log are kept; running sessions were not touched.")
    return 0


def trust_cmd():
    arg = (ARGS or [""])[0]
    if arg == "on":
        save_cfg_key("answer_trust", True)
    elif arg == "off":
        save_cfg_key("answer_trust", False)
    elif arg:
        print("say: trust on, or trust off")
        return 2
    if CFG["answer_trust"]:
        print("Trust answering is ON. When Tardigrade brings a session back and Claude Code asks \"do you trust this "
              "folder?\" for the folder that session was already running in, Tardigrade answers Yes for you. It answers "
              "no other question. Switch it off with: trust off")
    else:
        print("Trust answering is OFF. Tardigrade answers no question. A session that is asked \"do you trust this "
              "folder?\" waits there until you answer; the census marks it trust?. Switch it on with: trust on")
    return 0


COMMANDS = {"trust": trust_cmd, "record": record, "revive": revive, "census": census, "status": census, "open": open_windows,
            "setup": setup, "uninstall": uninstall}

if __name__ == "__main__":
    cmd = (sys.argv[1:] or ["census"])[0]
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        sys.exit(0)
    if cmd == "--version":
        print(VERSION)
        sys.exit(0)
    if cmd not in COMMANDS:
        print(f"unknown command: {cmd}\n{__doc__}")
        sys.exit(2)
    load_cfg()
    sys.exit(COMMANDS[cmd]() or 0)
