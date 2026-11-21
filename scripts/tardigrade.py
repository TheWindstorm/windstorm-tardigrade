#!/usr/bin/env python3
"""Windstorm Tardigrade: bring Claude Code sessions back after a reboot or a crash, and say what is running.

  record              write down every live session (a timer runs this every 5 minutes)
  revive [--boot]     start every recorded session that is not running (runs once after boot)
  revive --only NAME  bring back just that one
  census [--json]     what is running, what runs with no window open, what is down
  open                open a terminal window for each hidden session
  setup / uninstall   install or remove the boot service and the record timer
  --dry-run           with record, revive, open or setup: say what would happen, change nothing

Rules it keeps:
  * A session comes back as itself: `claude --resume <its own id>` in its own folder, with its own
    model, effort and permission mode. Its name and its whole conversation come back with it.
  * Never a second copy. A session id that any live process on this machine holds is never started.
  * It never ends a session, never types into one, never answers a dialog for you, and never stores
    a token or a key. For a login token it keeps the variable's name and an 8-digit one-way fingerprint,
    so it can tell whether a session came back on the same login.
  * The record is never overwritten by a half-started machine, and never by an empty list.
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

VERSION = "0.1.3"
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
}

WAKE_NOTE = (
    "Windstorm Tardigrade: this machine restarted and your session was brought back at {when}. "
    "Your conversation is intact. Anything you had running in the background is gone: timers, "
    "watchers, background commands and booked slots. Read your handoff or notes if you keep them, "
    "check what was in progress, start again only what is still needed, then carry on where you "
    "left off. If nothing was in progress, say so in one line and wait."
)

TOKEN_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
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
    """pid -> (parent pid, user id, program name)"""
    rc, out = run(["ps", "-axo", "pid=,ppid=,uid=,comm="])
    res = {}
    for line in out.splitlines():
        p = line.split(None, 3)
        if len(p) == 4 and p[0].isdigit():
            res[int(p[0])] = (int(p[1]), int(p[2]), os.path.basename(p[3].strip()))
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
    """Facts from a process's environment. No value is kept except one folder path. A login token is
    reduced, in memory, to 8 hex digits of its SHA-256: enough to tell "same login or not", useless for
    getting the token back."""
    facts = {"child": False, "token_keys": [], "config_dir": "", "token_print": ""}
    tok = []
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
    return os.path.join(tmux_dir(), "tardigrade")


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
    for pid, (_, uid, comm) in sorted(P.items()):
        if not is_claude(comm):
            continue
        if uid != me:
            others += 1        # counted, never read
            continue
        ef = env_facts(pid)
        sj = session_json(pid, ef["config_dir"])
        sid = sj.get("sessionId")
        if not sid:
            continue           # not a session: a version check, a helper process
        kind, detail = where(pid, P, PN)
        interactive = sj.get("kind", "interactive") == "interactive"
        if ef["child"] or not interactive:
            helpers += 1       # started by another session, or not interactive: not a lane
            continue
        row = {
            "pid": pid, "session_id": sid, "name": (sj.get("name") or "").strip(),
            "cwd": sj.get("cwd") or HOME, "host": kind, "config_dir": ef["config_dir"],
            "token_keys": ef["token_keys"], "token_print": ef["token_print"], "entrypoint": sj.get("entrypoint", "cli"),
            "status": sj.get("status", ""), "updated_at": int((sj.get("updatedAt") or 0) / 1000),
            "tmux_socket": "", "tmux_session": "", "attached": 0, "herdr_tab": "", "herdr_workspace": "",
        }
        if kind == "tmux":
            row["tmux_socket"], row["tmux_session"], row["attached"] = detail
        row["window"] = (kind in ("terminal", "ide", "ssh", "herdr")) or (kind == "tmux" and row["attached"] > 0)
        # a session with no terminal of any kind that the CLI did not start is some program's own: not ours to revive
        row["restorable"] = kind != "none" or row["entrypoint"] == "cli"
        if with_launch:
            row.update(launch_facts(pid, sj, ef["config_dir"]))
        mine.append(row)
    return mine, others, helpers


def running_sids():
    """every session id held by a live Claude of this user, in any terminal"""
    held = set()
    for pid, (_, uid, comm) in procs().items():
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
    for r in mine:
        if not r["restorable"]:
            continue
        if r["host"] == "tmux" and os.path.basename(r["tmux_socket"]) in skip:
            continue           # another tool brings these back: one restorer per session
        o = old_by.get(r["session_id"], {})
        tab, ws = (labels[0].get((r["tmux_socket"], r["tmux_session"])) or labels[1].get(r["pid"])
                   or (o.get("herdr_tab", ""), o.get("herdr_workspace", "")))
        if r["host"] == "herdr" and not tab:
            tab = r["name"]
        if r["host"] == "tmux":
            sock, name = r["tmux_socket"], r["tmux_session"]
        else:   # not in tmux today: it comes back in Tardigrade's own tmux, so it outlives its window
            sock, name = own_socket(), slug(r["name"]) or f"claude-{r['session_id'][:8]}"
            if (sock, name) in used:
                name = f"{name}-{r['session_id'][:4]}"
        used.add((sock, name))
        rows.append({
            "name": r["name"], "session_id": r["session_id"], "cwd": r["cwd"], "model": r["model"],
            "effort": r["effort"], "mode": r["mode"], "add_dirs": r["add_dirs"], "host": r["host"],
            "tmux_socket": sock, "tmux_session": name, "herdr_tab": tab, "herdr_workspace": ws,
            "config_dir": r["config_dir"], "token_keys": r["token_keys"], "token_print": r["token_print"],
            "last_seen": int(time.time()),
        })
    if not rows and old["lanes"]:
        log(f"record: 0 live sessions; kept the previous record ({len(old['lanes'])} sessions)")
        return 0
    now, keep = int(time.time()), int(CFG["down_keep_days"]) * 86400
    live_ids = {r["session_id"] for r in rows}
    down = [d for d in old["down"] if d["session_id"] not in live_ids and now - d.get("down_since", now) < keep]
    known_down = {d["session_id"] for d in down}
    for r in old["lanes"]:
        if r["session_id"] not in live_ids and r["session_id"] not in known_down:
            down.append(dict(r, down_since=now))
    if DRY:
        for r in rows:
            log("record (dry): " + json.dumps(r, sort_keys=True))
        return 0
    if os.path.exists(TABLE):
        shutil.copyfile(TABLE, TABLE + ".prev")
    write_json(TABLE, {"version": 1, "recorded_at": now, "boot": boot_id(), "lanes": rows, "down": down})
    log(f"record: {len(rows)} session(s), {len(down)} down -> {TABLE}")
    return 0


# ---------- revive ----------
def launch_cmd(r, note):
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
    sh = CFG["shell"]
    # A tmux server first started from inside a Claude session carries that session's environment.
    # CLAUDE_CODE_CHILD_SESSION turns transcript saving off, so none of it may reach a revived session.
    pre = ("unset CLAUDECODE CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_SESSION_ID CLAUDE_PID CLAUDE_EFFORT "
           "CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH CLAUDE_CODE_SESSION_ATTENDED "
           "CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN; ")
    if r.get("config_dir"):
        pre += f"export CLAUDE_CONFIG_DIR={shlex.quote(r['config_dir'])}; "
    parts = ["claude"] + [shlex.quote(x) for x in flags] + ["--resume", r["session_id"]]
    if note:
        parts.append(shlex.quote(note))
    inner = f"{pre}{' '.join(parts)}; echo; echo \"[session ended; shell kept]\"; exec {sh} -l"
    return f"{sh} -{'l' if sh == 'zsh' else ''}ic {shlex.quote(inner)}"


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


def start_lane(r, note, created):
    sock, name = r["tmux_socket"], r["tmux_session"]
    cmd = launch_cmd(r, note)
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
        argv = ["env", "-i"] + [f"{k}={v}" for k, v in clean_env().items()]
        argv += [CFG["tmux"], "-S", sock, "new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", cwd, cmd]
        rc = run(argv)[0]
        created.add((sock, name))
    return rc == 0


def came_up(r):
    rc, out = tmux(r["tmux_socket"], "list-panes", "-t", f"={r['tmux_session']}", "-F", "#{pane_pid}")
    if rc != 0 or not out.split():
        return None
    c = claude_under(int(out.split()[0]), procs())
    return c if c and alive(c) else None


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
    try:
        os.mkdir(lock)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock) < 7200:
            log("revive: another revive is running (lock); nothing done")
            return 0
        os.rmdir(lock)
        os.mkdir(lock)
    try:
        return _revive(boot)
    finally:
        os.rmdir(lock)


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
    # More than one login on this machine (some sessions with a token, some without, or two tokens) means
    # sessions are pinned to accounts. A pinned session comes back silent, and its login is checked before
    # anything can run on the wrong account.
    mixed = len({login_print(x) for x in read_table()["lanes"]}) > 1
    quiet = set()
    for r in rows:
        nm = display(r)
        if r["session_id"] in held:
            log(f"skip  {nm}: already running on this machine")
            report["skipped"].append(nm)
            continue
        if has_session(r["tmux_socket"], r["tmux_session"]):
            log(f"skip  {nm}: a tmux session named {r['tmux_session']} already exists")
            report["skipped"].append(nm)
            continue
        if mixed and login_print(r):
            quiet.add(r["session_id"])
        log(f"start {nm} ({r['session_id'][:8]}, {r['mode']}, {r.get('model') or 'default model'}) in {r['cwd']}"
            + ("  [pinned login: comes back silent, then its login is checked]" if r["session_id"] in quiet else ""))
        todo.append(r)
    if DRY:
        return 0
    created = set()
    pending = {r["session_id"]: [r, 0] for r in todo}
    deadline = time.time() + float(CFG["retry_min"]) * 60
    while pending:
        for sid, (r, n) in list(pending.items()):
            nm = display(r)
            if n and came_up(r):
                log(f"  {nm}: up (came up late)")
                report["started"].append(nm)
                del pending[sid]
                continue
            if sid in running_sids():
                if n and came_up(r):
                    log(f"  {nm}: up (came up late)")
                    report["started"].append(nm)
                else:
                    log(f"  {nm}: something else started it meanwhile; left alone")
                    report["skipped"].append(nm)
                del pending[sid]
                continue
            if start_lane(r, "" if sid in quiet else note, created):
                time.sleep(float(CFG["settle_s"]))
                pid = came_up(r)
                if pid:
                    if same_login(r, pid):
                        log(f"  {nm}: up" + (" (silent; same login as before)" if sid in quiet else ""))
                        report["started"].append(nm)
                    else:
                        log(f"needs you  {nm}: it came back on a different login than it had before. "
                            + ("It is idle and nothing ran. " if sid in quiet else "")
                            + "Close it and start it your usual way.")
                        report["needs_you"].append(nm)
                    del pending[sid]
                    time.sleep(float(CFG["stagger_s"]))
                    continue
            pending[sid][1] += 1
            log(f"  {nm}: not running yet (attempt {pending[sid][1]}); will retry")
            time.sleep(float(CFG["stagger_s"]))
        if not pending or time.time() >= deadline:
            break
        time.sleep(60 if not TESTING else 1)
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
    notify(f"{len(report['started'])} session(s) brought back, {len(report['failed']) + len(report['needs_you'])} need you")
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


def attach_tabs(rows):
    """Herdr only: put each revived session back in the tab that carries its label."""
    want = [r for r in rows if r.get("herdr_tab")]
    if not want or not CFG["herdr"] or not CFG["herdr_tabs"]:
        return
    end = time.time() + float(CFG["attach_wait_min"]) * 60
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
        "waiting": len([r for r in mine if r["status"] == "waiting"]),
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
    print(f"Comes back after a reboot: {'yes, by itself' if d['auto_revive'] else 'NO (run setup)'}   Last record: {rec}")
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
    if d["other_users"]:
        print(f"\nOther users on this machine: {d['other_users']} Claude process(es). Not yours; not read.")
    if d["helpers"]:
        print(f"Helper sessions started by other sessions: {d['helpers']} (not brought back after a reboot).")
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


def who_logs_in():
    """Say plainly what must happen after a restart before sessions can come back."""
    out = []
    if MAC:
        fv = run(["fdesetup", "status"], timeout=10)[1]
        auto = run(["defaults", "read", "/Library/Preferences/com.apple.loginwindow", "autoLoginUser"], timeout=10)
        if "FileVault is On" in fv:
            out.append("FileVault is on: after a restart this Mac waits at its unlock screen, and NOTHING comes back "
                       "until a person types the password there.")
        elif auto[0] == 0 and auto[1].strip():
            out.append("This Mac logs in by itself after a restart, so sessions come back with nobody there.")
        else:
            out.append("After a restart, sessions come back when someone logs in to this Mac.")
    else:
        user = os.environ.get("USER") or os.path.basename(HOME)
        linger = run(["loginctl", "show-user", user, "-p", "Linger", "--value"], timeout=10)[1].strip()
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


COMMANDS = {"record": record, "revive": revive, "census": census, "status": census, "open": open_windows,
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
