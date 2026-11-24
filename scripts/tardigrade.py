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
  report              the last restore's timeline, and what still needs a person
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
  * Other users' sessions on a shared machine are counted by program name; nothing else of them is read by
    this code (on Linux the system's ps, run for the process list, opens more of every process).

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

VERSION = "0.1.9"
HOME = os.path.expanduser("~")
MAC = sys.platform == "darwin"
CFG_DIR = os.path.join(HOME, ".config", "windstorm-tardigrade")
STATE = os.path.join(HOME, ".local", "state", "windstorm-tardigrade")
TABLE = os.path.join(CFG_DIR, "lanes.json")
SKIP_PATH = os.path.join(STATE, "record-skipped.json")   # a record skipped on a failed read: since when, and why (T55)
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
    "boot_wait_s": 45,        # the boot service waits this long before its run (written into the unit by setup)
    "stagger_s": 3,           # pause between two session starts
    "settle_s": 8,            # wait this long before checking that a start worked
    "starts_at_once": 4,      # at most this many sessions started and not yet seen up; the waits overlap
    "retry_min": 25,          # keep retrying failed starts this long (the network may lag at boot)
    "retry_gap_s": 60,        # between retry rounds; a late start is looked at in every pass meanwhile (G2.44)
    "wake": "note",           # "note": tell each revived session what happened; "idle": say nothing
    "wake_names": [],         # patterns: sessions whose name matches get the note even with "wake": "idle"
    "wake_skip": [],          # patterns: sessions whose name matches never get the note
    "wake_text": "",          # your own words for the note; {when} is the time it came back; "" = the default
    "notes_per_min": 6,       # at most this many notes typed in any one minute, so they do not all take a turn at once
    "auto_revive": False,     # set by setup: the boot service is installed
    "herdr_tabs": True,       # re-attach Herdr tabs after a revive
    "attach_wait_min": 30,    # wait this long for the Herdr server after boot
    "down_keep_days": 14,     # how long a session that went down stays on the "down" list
    "skip_tmux_sockets": [],  # tmux socket names to leave alone, when another tool brings those sessions back
    "skip_cwd_prefix": [],    # folders to leave alone: of a claude process working inside one, only its folder is read
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
    "on_done": "",            # a command run when a restore ends, with the counts: {started} {loaded} {not_loaded} {down} {failed}
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
# set by systemd for a service; a boot service's tmux server can carry them, and they are no session's own
UNIT_VARS = ("INVOCATION_ID JOURNAL_STREAM SYSTEMD_EXEC_PID MANAGERPID NOTIFY_SOCKET LISTEN_PID LISTEN_FDS "
             "LISTEN_FDNAMES WATCHDOG_PID WATCHDOG_USEC")
COLS = ["session", "session_id", "cwd", "model", "effort", "mode", "tab_label", "workspace_label"]
# never taken from an env_file: they belong to the shell that is starting, not to the one the file came from
NEVER_CARRY = {"PWD", "OLDPWD", "UID", "EUID", "PPID", "SHLVL", "_", "TERM", "TMUX", "TMUX_PANE", "BASHOPTS", "SHELLOPTS"}
TERMINALS = ("gnome-terminal", "konsole", "kitty", "alacritty", "wezterm", "xterm", "tilix", "xfce4-terminal",
             "ptyxis", "kgx", "foot", "ghostty", "terminator", "iTerm", "Terminal", "Warp", "warp", "tabby")
IDES = ("code", "Code", "cursor", "Cursor", "zed", "idea", "pycharm", "webstorm", "goland", "windsurf")
SHELLS = ("bash", "zsh", "sh", "-bash", "-zsh", "fish", "dash")


# ---------- small tools ----------
def private_dirs():
    """Tardigrade's own two folders are this user's only (0700): the record, the log, the start files and the
    boot agent's output hold start flags word for word, and a home folder is not always closed (a Mac's is 755)."""
    for d in (STATE, CFG_DIR):
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)
        except OSError:
            pass


def log(msg):
    os.makedirs(STATE, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line, flush=True)
    fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(argv, timeout=30, env=None):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return 99, ""


PATH_SET = [False]      # the path setting was given: then it is used exactly as written


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
    PATH_SET[0] = bool(CFG["path"])
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


def write_private(path, text):
    """Write a file that this user only can read (0600), whole or not at all. The record holds start flags
    word for word, and a home folder or ~/.config is not always closed to other users (a Mac's is 755)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)                  # a .new left from before keeps its old mode otherwise
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path, obj):
    write_private(path, json.dumps(obj, indent=1, sort_keys=True) + "\n")


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
class Procs(dict):
    """A process list. ok is False when the read failed (ps gave an error, or no answer within 30 s): then it is
    empty, and empty there means "cannot tell", never "nothing runs" (T52, T53). why says what failed."""
    ok, why = True, ""


def procs():
    """pid -> (parent pid, user id, program name). A process that has ended and only waits to be collected
    (a zombie) is left out: it holds no session."""
    rc, out = run(["ps", "-axo", "pid=,ppid=,uid=,stat=,comm="])
    res = Procs()
    if rc != 0 or not out.strip():       # ps always lists itself: an empty answer is a failed read too
        res.ok = False
        res.why = "the process list could not be read (ps " + (
            "gave no answer within 30 s, or could not be started)" if rc == 99 else f"ended with code {rc})")
        return res
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
    a = mac_argv(pid)
    return a if a is not None else run(["ps", "-o", "args=", "-p", str(pid)])[1].split()


def mac_argv(pid):
    """macOS: a process's own argument list, from the kernel (sysctl KERN_PROCARGS2), so that an argument
    with spaces in it stays one argument. None when it cannot be read; `ps` is then the fallback."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, int(pid))          # CTL_KERN, KERN_PROCARGS2, pid
        size = ctypes.c_size_t(1 << 18)
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
        raw = buf.raw[:size.value]
        argc = int.from_bytes(raw[:4], sys.byteorder)
        rest = raw[4:]
        rest = rest[rest.index(b"\0"):].lstrip(b"\0")      # skip the program's path and its padding
        parts = rest.split(b"\0")[:argc]
        return [x.decode(errors="replace") for x in parts] if len(parts) == argc else None
    except Exception:
        return None


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


# G3.28 (T79): the tool overwrites or removes only what it wrote. A file is known as ours by a mark it carries,
# or, for Python's cache, by the name Python gives the cache of our script. Anything else in our folders is the
# person's: it is never written over or removed.
MARKS = {
    "copy": '"""Windstorm Tardigrade: bring Claude Code sessions back',     # every version's second line
    "unit": "Description=Windstorm Tardigrade:",
    "plist": "<string>ai.thewindstorm.tardigrade-",
    "start": "# Windstorm Tardigrade start file.",
    "marker": "",          # a boot marker: known by its name alone (revived-<boot id>), see is_ours
}
OUR_CACHE = re.compile(r"tardigrade\.[A-Za-z0-9_.-]+\.pyc")


def is_ours(path, kind):
    """True only when the file at path is one Tardigrade wrote: a plain file (never a link or a folder) that
    carries our mark. Unreadable is not ours."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return False
        if kind == "cache":
            return bool(OUR_CACHE.fullmatch(os.path.basename(path)))
        if kind == "marker":
            return bool(re.fullmatch(r"revived-[A-Za-z0-9-]{1,64}", os.path.basename(path)))
        with open(path, "rb") as f:
            head = f.read(4096).decode("utf-8", "replace")
    except OSError:
        return False
    if kind == "start":
        return head.startswith(MARKS["start"])
    return MARKS[kind] in head


MARKERS_KEPT = 5            # G2.42, T38: only the newest few boot markers; an old boot's is never read again


def write_marker(text):
    """this boot's marker, this user's only (0600); the markers of all but the newest few boots are removed"""
    write_private(marker(), text)
    try:
        old = sorted((p for p in glob.glob(os.path.join(STATE, "revived-*")) if p != marker() and is_ours(p, "marker")),
                     key=lambda p: os.path.getmtime(p), reverse=True)
    except OSError:
        return
    for p in old[MARKERS_KEPT - 1:]:
        try:
            os.remove(p)
        except OSError:
            pass


def user_services_up_s():
    """Seconds after boot at which this user's service manager started (Linux, systemd), or None. A boot
    run cannot begin before it, and on some machines the system's own start holds it back for minutes."""
    if TESTING and os.environ.get("TARDIGRADE_FAKE_USER_UP_S"):
        return float(os.environ["TARDIGRADE_FAKE_USER_UP_S"])
    if MAC:
        return None
    try:
        ppid = os.getppid()
        with open(f"/proc/{ppid}/comm") as f:
            if f.read().strip() != "systemd":
                return None
        with open(f"/proc/{ppid}/stat") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        return ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


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


def tmux(sock, *a, timeout=10, env=None):
    if not CFG["tmux"]:
        return 99, ""
    return run([CFG["tmux"], "-S", sock, *a], timeout=timeout, env=env)


class Panes(dict):
    """The tmux panes. ok is False when a server that is there could not list them: then where any session
    lives is not known (T55). A socket left by a server that is gone (after a reboot) is not a failed read."""
    ok, why = True, ""


def record_sockets():
    """the tmux sockets whose pane list the record depends on: every socket a recorded row lives on, tmux's own
    default, this tool's own (loose_socket) and the table's (tsv_socket). A wedged server elsewhere (a test socket,
    another program's) does not stop the record (T55, from the review: it would freeze a machine's record for days)."""
    w = {os.path.join(tmux_dir(), "default"), own_socket()}
    if CFG.get("tsv_socket"):
        w.add(os.path.join(tmux_dir(), os.path.basename(str(CFG["tsv_socket"]))))
    t = read_json(TABLE, {})
    if isinstance(t, dict):
        w |= {r.get("tmux_socket") for r in t.get("lanes", []) + t.get("down", []) if isinstance(r, dict)}
    return w


def panes():
    """pane pid -> (socket, tmux session name, windows attached). Read only."""
    res = Panes()
    ours = None
    for s in sockets():
        rc, out, err = 99, "", ""
        if CFG["tmux"]:
            try:
                r = subprocess.run([CFG["tmux"], "-S", s, "list-panes", "-a", "-F",
                                    "#{pane_pid}\t#{session_attached}\t#{session_name}"],
                                   capture_output=True, text=True, timeout=6)
                rc, out, err = r.returncode, r.stdout, r.stderr
            except (subprocess.TimeoutExpired, OSError):
                pass
        if rc != 0:
            ours = record_sockets() if ours is None else ours
            # tmux's own words for a socket nobody listens on (a server gone at a reboot): "no server running on",
            # or in older versions "error connecting to". Anything else, or no answer, is a failed read (T55).
            stale = rc != 99 and ("no server running" in err or "error connecting to" in err)
            if s in ours and not stale:
                res.ok = False
                res.why = f"the tmux server at {s} did not list its panes (" + (
                    "no answer within 6 s)" if rc == 99 else f"tmux ended with code {rc})")
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


EXIT_CMD = re.compile(r"<command-name>/(exit|quit)</command-name>")


def print_run(r):
    """G2.47: the entrypoint this session's conversation last names, when it is a print-mode or SDK run's
    ("sdk-cli", "sdk-ts", ...), else "". A session a person sits in says "cli". The last one counts: a print-mode
    conversation that someone later resumed by hand in a terminal is a session again."""
    base = r.get("config_dir") or os.path.join(HOME, ".claude")
    for f in glob.glob(os.path.join(base, "projects", "*", f"{r['session_id']}.jsonl")):
        try:
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                n = fh.tell()
                fh.seek(max(0, n - 65536))
                chunks = [fh.read()]
                if n > 65536:
                    fh.seek(0)
                    chunks.insert(0, fh.read(65536))
        except OSError:
            continue
        last = ""
        for chunk in chunks:
            for line in chunk.decode(errors="replace").splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and isinstance(d.get("entrypoint"), str) and d["entrypoint"]:
                    last = d["entrypoint"]
        return last if last.startswith("sdk") else ""
    return ""


def last_turn(r):
    """The last entry of this session's conversation that is a turn: a user or assistant line, not Claude
    Code's own bookkeeping and not what it printed for a command (its "Bye!" for /exit). None if there is
    no conversation file or no such line."""
    base = r.get("config_dir") or os.path.join(HOME, ".claude")
    for f in glob.glob(os.path.join(base, "projects", "*", f"{r['session_id']}.jsonl")):
        try:
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 65536))
                tail = fh.read().decode(errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(tail):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("type") not in ("user", "assistant") or d.get("isMeta"):
                continue
            c = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
            if isinstance(c, str) and c.lstrip().startswith(("<local-command-stdout>", "<local-command-stderr>")):
                continue
            return d
        return None
    return None


_HISTORY = {}
EXIT_SIDS = []                  # the session ids on the record, so the history is read once for all of them
HISTORY_SAY = []                # set by revive and record, which say in the log when the history cannot be used
HISTORY_SID = re.compile(r'"sessionId"\s*:\s*"([0-9A-Za-z-]{8,64})"')


def history_exits(base, sids):
    """{session id: the time of its /exit or /quit, when that is the last prompt typed into it} from Claude Code's
    prompt history in the folder `base`, for the session ids `sids` only (G2.36). Claude Code writes every prompt
    typed into a session there, slash commands too, with the session id; it does so even when it writes no
    /exit into the conversation itself (seen on real machines with 2.1.330). The file holds what people typed:
    a line of another session is skipped before it is read, of a line of these only the time and whether it is
    exactly /exit or /quit are kept, and nothing of it is logged, recorded or shown. When the file is missing or
    does not look as expected, no session is taken as ended by it, and the log says the check could not be made."""
    have = _HISTORY.get(base)
    if have is not None and set(sids) <= have[0]:
        return have[1]
    want = set(sids) | (have[0] if have else set())
    last, shaped, problem = {}, 0, ""
    try:
        with open(os.path.join(base, "history.jsonl"), "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 4_000_000))
            tail = fh.read().decode(errors="replace").splitlines()
    except OSError as e:
        tail, problem = [], f"it cannot be read ({e.__class__.__name__})"
    for line in tail:
        m = HISTORY_SID.search(line)
        if not m:
            continue
        shaped += 1
        if m.group(1) not in want:
            continue                    # another session's: not read
        try:
            d = json.loads(line)
            sid, t, words = d["sessionId"], float(d["timestamp"]) / 1000, d.get("display")
        except (ValueError, KeyError, TypeError):
            continue
        if sid in want and isinstance(words, str) and t >= last.get(sid, (0, False))[0]:
            last[sid] = (t, words.strip() in ("/exit", "/quit"))
    if not problem and tail and not shaped:
        problem = "it does not look as expected (no session ids in it)"
    if problem and have is None and HISTORY_SAY:
        log(f"the prompt history in {base} could not be used: {problem}. A session ended with /exit is known only "
            "from its conversation or its pane; one may be brought back that its owner ended.")
    out = {sid: t for sid, (t, ex) in last.items() if ex}
    _HISTORY[base] = (want, out)
    return out


def exited_at(r):
    """When this session's owner ended it with /exit (or /quit), as a Unix time, or 0, if that was after a record
    last saw the session running (an /exit older than that was followed by a resume: a session moved to another
    account does exactly that). Two places say it: Claude Code's prompt history, which has every /exit typed,
    and the conversation, whose last turn is then that command (Claude Code's "Bye!" after it does not count)."""
    import datetime
    base = r.get("config_dir") or os.path.join(HOME, ".claude")
    seen = float(r.get("last_seen") or 0)
    t = history_exits(base, [r["session_id"]] + EXIT_SIDS).get(r["session_id"], 0)
    if t > seen:
        return t
    d = last_turn(r)
    c = (d.get("message") or {}).get("content") if d and isinstance(d.get("message"), dict) else None
    if not (isinstance(c, str) and EXIT_CMD.search(c)):
        return 0
    try:
        t = datetime.datetime.fromisoformat(str(d.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0
    return t if t and t > seen else 0


LIMIT_MARGIN_S = 300            # G2.48: a reset counts as passed this long after the moment it names
MONTHS = {m: i + 1 for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}
RESETS = re.compile(r"resets (?:(%s) (\d{1,2}), )?(\d{1,2})(?::(\d{2}))?\s*(am|pm) \(([A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*)\)"
                    % "|".join(MONTHS), re.I)


def limit_reset(text, written):
    """G2.48: the moment a limit reply's "resets ..." words name, as a Unix time, or None when it cannot be read.
    It is worked out from the reply's own time and the zone it names, never from today's clock: "resets 12pm
    (Zone)" is the first 12pm in that zone after the reply was written; "resets Nov 24, 6pm (Zone)" is that day."""
    import datetime
    m = RESETS.search(text or "")
    if not m or not written:
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(m.group(6))
        at = datetime.datetime.fromtimestamp(float(written), tz)
        hour = int(m.group(3)) % 12 + (12 if m.group(5).lower() == "pm" else 0)
        minute = int(m.group(4) or 0)
        if hour > 23 or minute > 59:
            return None
        if m.group(1):
            mon = MONTHS[m.group(1).title()]
            t = datetime.datetime(at.year, mon, int(m.group(2)), hour, minute, tzinfo=tz)
            if t < at - datetime.timedelta(days=1):          # a day early in the next year: "resets Jan 2" in December
                t = datetime.datetime(at.year + 1, mon, int(m.group(2)), hour, minute, tzinfo=tz)
        else:
            t = at.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if t <= at:
                t += datetime.timedelta(days=1)
        return t.timestamp()
    except Exception:          # no zone database, an unknown zone, a day that does not exist: it cannot be read
        return None


def limit_reply(r):
    """G2.45, G2.48: (its words, when it was written) for the usage or limit reply this session's conversation
    last ended on, or ("", 0)"""
    import datetime
    words = ended_on_limit(r)
    if not words:
        return "", 0
    try:
        t = datetime.datetime.fromisoformat(str(last_turn(r).get("timestamp", "")).replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        t = 0
    return words, t


def ended_on_limit(r):
    """G2.45: the words of the usage or limit reply this session's conversation last ended on, or "". Claude
    Code writes that reply as an assistant line marked as an error ("You've hit your weekly limit · resets
    12pm"). A note typed into such a session is not lost: Claude Code holds it and runs it by itself when the
    limit resets, hours later, with nobody there and its words stale."""
    d = last_turn(r)
    if not d or d.get("type") != "assistant" or not d.get("isApiErrorMessage"):
        return ""
    c = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
    text = c if isinstance(c, str) else " ".join(str(x.get("text") or "") for x in (c or []) if isinstance(x, dict))
    if d.get("error") == "rate_limit" or OTHER_DIALOG.search(text):
        return " ".join(text.split())[:120] or "a usage limit"
    return ""


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
    extra, dropped = start_flags(a)
    return {"model": model, "effort": opt("--effort"), "mode": mode, "add_dirs": add_dirs,
            "flags": extra, "dropped": dropped}


# Claude Code's start flags (2.1.330), by what follows them. Read once from `claude --help`; a flag not in
# these lists is still carried when its form is plain, and named in the log when it is not (G2.39).
FLAG_ONE = {"--agent", "--agents", "--append-system-prompt", "--append-system-prompt-file", "--autocompact",
            "--debug-file", "--effort", "--environment", "--fallback-model", "--input-format", "--json-schema",
            "--max-budget-usd", "--model", "-n", "--name", "--output-format", "--permission-mode",
            "--permission-prompts", "--plugin-dir", "--plugin-url", "--remote-control-session-name-prefix",
            "--session-id", "--setting-sources", "--settings", "--system-prompt", "--system-prompt-file",
            "--system-prompt-snapshot"}
FLAG_MANY = {"--add-dir", "--allowedTools", "--allowed-tools", "--betas", "--disallowedTools",
             "--disallowed-tools", "--file", "--mcp-config", "--tools"}
FLAG_NONE = {"--allow-dangerously-skip-permissions", "--ax-screen-reader", "--bg", "--background", "--bare",
             "--brief", "--chrome", "-c", "--continue", "--dangerously-skip-permissions", "--desktop",
             "--disable-slash-commands", "--exclude-dynamic-system-prompt-sections", "--fork-session",
             "--forward-subagent-text", "-h", "--help", "--ide", "--include-hook-events",
             "--include-partial-messages", "--no-chrome", "--no-session-persistence", "-p", "--print",
             "--replay-user-messages", "--restricted", "--safe-mode", "--strict-mcp-config", "-v", "--version",
             "--verbose"}
FLAG_MAYBE = {"-d", "--debug", "--cloud", "--from-pr", "--prompt-suggestions", "--remote-control", "-r",
              "--resume", "--teleport", "-w", "--worktree", "--tmux"}
# carried another way (the permission mode, model, effort and added folders have fields of their own)
FLAG_OWN = {"--dangerously-skip-permissions", "--permission-mode", "--model", "--effort", "--add-dir"}
# they name a conversation or pass a first message: a revive gives its own --resume
FLAG_CONVERSATION = {"-r", "--resume", "-c", "--continue", "--session-id", "--fork-session", "--from-pr",
                     "--teleport", "-p", "--print"}
# they would start something other than this session in this place
FLAG_ELSEWHERE = {"--bg", "--background", "--desktop", "--cloud", "--environment", "-w", "--worktree", "--tmux",
                  "-v", "--version", "-h", "--help"}
# a value that names a secret, or has the shape of a common one (keys and tokens of the usual services, a JWT)
SECRET_VALUE = re.compile(r"TOKEN|SECRET|PASSW|CREDENTIAL|API_?KEY|PRIVATE_?KEY|AUTHORI[SZ]ATION|BEARER", re.I)
SECRET_SHAPE = re.compile(r"sk-[A-Za-z0-9]|sk-ant-|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
                          r"xox[abprs]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
                          r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}|hf_[A-Za-z0-9]{30,}")
# they take JSON or a file: only a file's path is carried, never the JSON itself (it can hold anything)
FLAG_PATH_ONLY = {"--settings", "--mcp-config", "--agents"}
# carried, and said so in the log, because the session then comes back visibly different from 0.1.7's way
FLAG_SAID = {"--remote-control": "it comes back with Remote Control on, as it was started"}


def start_flags(a):
    """(flags to carry, word for word; names of flags not carried, each with why). a: the whole argv."""
    extra, dropped = [], []
    i = 1
    while i < len(a):
        x = a[i]
        if not x.startswith("-") or x == "-":
            i += 1                  # a first message: a revive resumes the conversation instead (never logged)
            continue
        if x == "--":
            break
        name, eq, _ = x.partition("=")
        vals = []
        if not eq:
            if name in FLAG_ONE:
                vals = a[i + 1:i + 2]
            elif name in FLAG_MANY or name in FLAG_MAYBE:
                j = i + 1
                while j < len(a) and not a[j].startswith("-"):
                    j += 1
                    if name in FLAG_MAYBE:
                        break
                vals = a[i + 1:j]
            elif name not in FLAG_NONE and i + 1 < len(a) and not a[i + 1].startswith("-"):
                dropped.append(f"{name} (not known to this version: is the word after it its value?)")
                i += 2
                continue
        words = [x] + vals
        i += len(words)
        if name in FLAG_OWN:
            continue
        if name in FLAG_CONVERSATION:
            continue
        if name in FLAG_ELSEWHERE:
            dropped.append(f"{name} (it would start something other than this session here)")
            continue
        if any(SECRET_VALUE.search(w) or SECRET_SHAPE.search(w) for w in words):
            dropped.append(f"{name} (its value looks like it holds a secret)")
            continue
        if name in FLAG_PATH_ONLY and any(w.lstrip()[:1] in ("{", "[") for w in (vals or [x.partition("=")[2]])):
            dropped.append(f"{name} (inline JSON: only a file's path is carried)")
            continue
        extra += words
    return extra, dropped




PANES_NOW = []      # this scan's tmux panes, for one_shot()
PROCS_NOW = []      # this scan's process list: its ok says whether the read worked (T53)
LEFT_ALONE = [0]    # this scan's claude processes in a skip_cwd_prefix folder; only their folder read (G1.15)


def proc_cwd(pid):
    """The folder a process works in, from the system, without reading anything of the process itself."""
    if not MAC:
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return ""
    out = run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], timeout=5)[1]
    return next((l[1:] for l in out.splitlines() if l.startswith("n")), "")


def in_skip_folder(path):
    """Is this folder inside one named in skip_cwd_prefix?"""
    pre = [os.path.realpath(os.path.expanduser(str(x))).rstrip("/") for x in (CFG.get("skip_cwd_prefix") or []) if x]
    if not pre or not path:
        return False
    path = os.path.realpath(path)
    return any(path == x or path.startswith(x + "/") for x in pre)


def left_alone(pid):
    """G1.15: is this process working inside a folder named in skip_cwd_prefix? Then this code reads nothing more of it:
    not its environment, not its arguments, not its status file or conversation. (On Linux the system's ps, which
    procs() runs for the whole list, opens more of every process; 0.1.11 reads /proc itself, T47.)"""
    return bool(CFG.get("skip_cwd_prefix")) and in_skip_folder(proc_cwd(pid))


def has_tty(pid):
    """Is this process's input a terminal? An interactive Claude Code session always reads one."""
    if not MAC:
        try:
            t = os.readlink(f"/proc/{pid}/fd/0")
        except OSError:
            return True            # cannot tell: do not leave a session out on a guess
        return t.startswith(("/dev/pts/", "/dev/tty", "/dev/console"))
    t = run(["ps", "-o", "tty=", "-p", str(pid)])[1].strip()
    return t not in ("", "?", "??")


def one_shot(pid, sj, P):
    """G1.14: a run that is not a session a person sits in is never a lane, wherever it runs: `claude -p` (print
    mode, which Claude Code still marks "interactive" in its status file), a run through an SDK, a process with
    no terminal on its input, and a process started inside another Claude session (its Bash tool, its pane)."""
    a = argv_of(pid)[1:]
    if any(x in ("-p", "--print") or x.startswith("--print=") for x in a):
        return True
    if str(sj.get("entrypoint") or "").startswith("sdk"):
        return True
    if not has_tty(pid):
        return True
    # Inside another session: a claude among its parents below the place it lives. The walk stops at the first
    # tmux pane, Herdr, terminal window, editor or ssh login: what lies above that (a Herdr server or a terminal
    # someone opened from a Claude session) does not make the sessions inside it anyone's helpers.
    PN = PANES_NOW[0] if PANES_NOW else {}
    p, steps = P.get(pid, (0, 0, ""))[0], 0
    while p in P and p > 1 and steps < 40:
        comm = P[p][2]
        if is_claude(comm):           # a claude that holds a pane itself: this run is inside that session
            return True
        if p in PN or comm == "herdr" or comm.startswith(TERMINALS + IDES + ("sshd",)):
            return False
        p, steps = P[p][0], steps + 1
    return False


def live(with_launch=True):
    """(my live sessions, number of other users' Claude processes, number of helper sessions)"""
    P, PN = procs(), panes()
    PANES_NOW[:], PROCS_NOW[:] = [PN], [P]
    me, mine, others, helpers = os.getuid(), [], 0, 0
    LEFT_ALONE[0] = 0
    table = read_json(TABLE, {})
    known = {r["session_id"]: r for r in (table.get("down", []) + table.get("lanes", []))} if isinstance(table, dict) else {}
    for pid, (_, uid, comm) in sorted(P.items()):
        if not is_claude(comm):
            continue
        if uid != me:
            others += 1        # counted by program name; nothing else read here
            continue
        if left_alone(pid):
            LEFT_ALONE[0] += 1     # in a folder to leave alone: counted; only its folder was read
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
        if ef["child"] or not interactive or one_shot(pid, sj, P):
            helpers += 1       # started by another session, not interactive, or a one-shot run: not a lane
            continue
        row = {
            "pid": pid, "session_id": sid, "name": (sj.get("name") or "").strip(),
            "cwd": sj.get("cwd") or HOME, "host": kind, "config_dir": ef["config_dir"],
            "token_keys": ef["token_keys"], "token_print": ef["token_print"], "entrypoint": sj.get("entrypoint", "cli"),
            "account": ef["account"], "carry_env": ef["carry"],
            "status": sj.get("status", ""), "updated_at": int((sj.get("updatedAt") or 0) / 1000),
            "started_at": int((sj.get("startedAt") or 0) / 1000),
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


def left_out_why(r):
    """Why record leaves this running session out, or "" when it is kept (G3.12 says it in the census)."""
    if not r["restorable"]:
        return "no terminal, and not started from the command line: some program's own session"
    if r["host"] == "tmux" and os.path.basename(r["tmux_socket"]) in set(CFG.get("skip_tmux_sockets") or []):
        return "its tmux socket is on skip_tmux_sockets: another tool brings it back"   # one restorer per session
    if r["host"] != "tmux" and any(re.search(p, r["name"]) for p in (CFG.get("loose_skip") or [])
                                   if isinstance(p, str)):
        return "a loose_skip pattern in the settings leaves it out"
    return ""


def running_sids(P=None):
    """every session id held by a live Claude of this user, in any terminal"""
    held = set()
    for pid, (_, uid, comm) in (P or procs()).items():
        if not is_claude(comm) or uid != os.getuid() or left_alone(pid):
            continue
        sid = session_json(pid, env_facts(pid)["config_dir"]).get("sessionId")
        if sid:
            held.add(sid)
        m = re.search(r"--resume[ =]([0-9a-f-]{36})", " ".join(argv_of(pid)))
        if m:
            held.add(m.group(1))
    return held


def holder_text(sid, P):
    """where the live process that holds this session id runs: its tmux session, else its process id"""
    PN = panes()
    for pid, (_, uid, comm) in P.items():
        if not is_claude(comm) or uid != os.getuid() or left_alone(pid):
            continue
        if session_json(pid, env_facts(pid)["config_dir"]).get("sessionId") == sid or re.search(
                r"--resume[ =]" + re.escape(sid), " ".join(argv_of(pid))):
            kind, detail = where(pid, P, PN)
            return f"tmux {os.path.basename(detail[0])}:{detail[1]}" if kind == "tmux" else f"process {pid}"
    return "another process"


def starts_finished():
    """G3.6, T37: the running revive has done every start (none is waiting to be started, retried or seen up).
    What it still waits for after that (loading, notes, tabs) does not hold the record back."""
    pr = read_json(os.path.join(STATE, "progress.json"), {})
    return (isinstance(pr, dict) and pr.get("boot") == boot_id() and alive(int(pr.get("pid") or 0))
            and bool(pr.get("starts_done") or pr.get("done")))


def run_going():
    """a revive is running now: its lock is held by a live process of this boot"""
    try:
        with open(os.path.join(STATE, "lock", "owner")) as f:
            o_boot, o_pid = f.read().split()
        return o_boot == boot_id() and alive(int(o_pid)) and int(o_pid) != os.getpid()
    except (OSError, ValueError):
        return False


def display(r):
    return r.get("name") or f"{os.path.basename(r.get('cwd') or '') or 'session'} ({r['session_id'][:8]})"


# ---------- herdr (optional) ----------
HERDR_WAIT = {"s": 0}     # setup raises it: on a loaded machine Herdr can be slow to answer (G5.6)


def herdr(*a, timeout=40):
    if not CFG["herdr"] or not CFG["herdr_tabs"]:
        return None
    rc, out = run([CFG["herdr"], *a], timeout=max(timeout, HERDR_WAIT["s"]))
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
    if not force and run_going() and not starts_finished():
        log("record: skipped (a revive is starting sessions; waiting screens are not sessions gone)")
        return 0
    mine, _, _ = live()
    # A failed read is "cannot tell", never "not running" or "no window": the process list and the pane list are
    # each one shared read, so when one failed nothing is known about any session, and nothing is written (T55).
    bad = [x.why for x in PROCS_NOW + PANES_NOW if not x.ok]
    if bad:
        log(f"record: skipped ({'; '.join(bad)}); the record is kept as it was")
        if not DRY:     # since when: census names a skip that has lasted an hour (from the review of T55)
            sk = read_json(SKIP_PATH, {})
            since = sk.get("since") if isinstance(sk, dict) and sk.get("since") else int(time.time())
            write_json(SKIP_PATH, {"since": since, "why": "; ".join(bad), "last": int(time.time())})
        return 0
    try:
        os.remove(SKIP_PATH)       # the reads work again
    except OSError:
        pass
    old = read_table()
    old_by = {r["session_id"]: r for r in old["lanes"]}
    labels = ({}, {})
    if "--no-herdr" not in ARGS and CFG["herdr"] and CFG["herdr_tabs"] and herdr_up():
        labels = herdr_labels()
    table_labels = tsv_labels() if CFG.get("tsv_path") else {}
    rows, used = [], set()
    for r in mine:
        if left_out_why(r):
            continue
        o = old_by.get(r["session_id"], {})
        # labels from, in order: Herdr now, the record before, the table at tsv_path (G5.6)
        tab, ws = (labels[0].get((r["tmux_socket"], r["tmux_session"])) or labels[1].get(r["pid"])
                   or ((o.get("herdr_tab", ""), o.get("herdr_workspace", "")) if o.get("herdr_tab") else None)
                   or table_labels.get(r["session_id"]) or ("", ""))
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
            "effort": r["effort"], "mode": r["mode"], "add_dirs": r["add_dirs"], "flags": r["flags"],
            "dropped": r["dropped"], "host": r["host"],
            "tmux_socket": sock, "tmux_session": name, "herdr_tab": tab, "herdr_workspace": ws,
            "config_dir": r["config_dir"], "token_keys": r["token_keys"], "token_print": r["token_print"],
            "account": r["account"], "carry_env": r["carry_env"],
            "last_seen": int(time.time()),
        })
    was = {r["session_id"]: r for r in old["lanes"]}
    for r in rows:              # a flag that cannot be carried is named once, when it is first seen (G2.39)
        o = was.get(r["session_id"], {})
        if r["dropped"] and r["dropped"] != o.get("dropped"):
            log(f"record: {display(r)}: start flag(s) not carried: {'; '.join(r['dropped'])}")
        for f, what in FLAG_SAID.items():
            if f in r["flags"] and f not in (o.get("flags") or []):
                log(f"record: {display(r)}: {f} is carried: {what}")
    if not rows and old["lanes"]:
        # A row for a print-mode run (one 0.1.8 wrote down) is not kept as a session even here (G2.47, T43).
        # A row in a folder left alone: its conversation is not read even for that (G1.15: seen with strace on a real machine).
        pr = [r for r in old["lanes"] if not in_skip_folder(r.get("cwd", "")) and print_run(r)]
        log(f"record: 0 live sessions; kept the previous record ({len(old['lanes']) - len(pr)} sessions)"
            + (f"; {len(pr)} print-mode run(s) moved to the down list: {', '.join(display(r) for r in pr)}" if pr else ""))
        if pr and not DRY:
            gone = {r["session_id"] for r in pr}
            now = int(time.time())
            write_json(TABLE, dict(old, lanes=[r for r in old["lanes"] if r["session_id"] not in gone],
                                   down=old["down"] + [dict(r, down_since=now, ended="print-run") for r in pr]))
        return 0
    now, keep = int(time.time()), int(CFG["down_keep_days"]) * 86400
    live_ids = {r["session_id"] for r in rows}
    down = [d for d in old["down"] if d["session_id"] not in live_ids and now - d.get("down_since", now) < keep
            and not in_skip_folder(d.get("cwd", ""))]      # a folder left alone now: forgotten, not down (G1.15)
    known_down = {d["session_id"] for d in down}
    # Sessions that vanish together did not each end by a person's hand: that is a crash (a tmux server
    # that died, a terminal program that was closed with everything in it). They stay on the record as
    # sessions to bring back for lost_keep_h hours. `record --force` says: what runs now is the truth.
    # A recorded session whose own tmux session now holds another session was replaced there (a new
    # conversation in the same place, or a session that came back new): it is not down, it is over.
    taken = {(r["tmux_socket"], r["tmux_session"]) for r in rows}
    gone = [r for r in old["lanes"] if r["session_id"] not in live_ids and r["session_id"] not in known_down
            and (r["tmux_socket"], r["tmux_session"]) not in taken and not in_skip_folder(r.get("cwd", ""))]
    # A session its owner ended with /exit (after a record last saw it running) is over, at once, and never
    # part of a crash count. "Its pane holds the shell it left" counts only for a session that went alone:
    # a memory kill of many sessions leaves shells behind too, and that is a crash.
    EXIT_SIDS[:], HISTORY_SAY[:] = [r["session_id"] for r in gone], [True]
    exits = {r["session_id"]: exited_at(r) for r in gone}
    fresh = [r for r in gone if not r.get("lost_since") and not exits[r["session_id"]]]
    crash = not force and len(fresh) >= 3 and len(fresh) * 2 > len(old["lanes"])
    lost = []
    for r in gone:
        if print_run(r):            # a print-mode run is never kept as lost, crash or not (G2.47)
            down.append(dict({k: v for k, v in r.items() if k != "lost_since"}, down_since=now, ended="print-run"))
            continue
        since = 0 if exits[r["session_id"]] else (r.get("lost_since") or (now if crash else 0))
        if since and not force and now - since < float(CFG["lost_keep_h"]) * 3600:
            lost.append(dict(r, lost_since=since))
        else:
            d = dict({k: v for k, v in r.items() if k != "lost_since"}, down_since=now)
            if exits[r["session_id"]]:
                d.update(ended="exit", ended_at=int(exits[r["session_id"]]))
            elif not crash and has_session(r["tmux_socket"], r["tmux_session"]) and tmux(
                    r["tmux_socket"], "display", "-p", "-t", f"={r['tmux_session']}:",
                    "#{pane_current_command}")[1].strip() in SHELLS:
                d.update(ended="shell")
            down.append(d)
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
        os.chmod(TABLE + ".prev", 0o600)       # the record's copy holds start flags too (G2.42)
    write_json(TABLE, {"version": 1, "recorded_at": now, "boot": boot_id(), "lanes": rows, "down": down})
    log(f"record: {len(rows)} session(s), {len(down)} down -> {TABLE}")
    write_tsv(rows)
    return 0


def tsv_labels():
    """session id -> (tab label, workspace label) from the table at tsv_path, which may have been written by
    another tool before this one took over. Read only; a table that is not there gives nothing."""
    out = {}
    try:
        with open(os.path.expanduser(str(CFG.get("tsv_path") or "")), encoding="utf-8") as f:
            for line in f:
                c = line.rstrip("\n").split("\t")
                if line.startswith("#") or len(c) < 8:
                    continue
                if c[1] and (c[6] or c[7]):
                    out[c[1]] = (clean_label(c[6]), clean_label(c[7]))
    except OSError:
        pass
    return out


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
        write_private(path, "\n".join(lines) + "\n")
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
    extra = [str(x) for x in (r.get("flags") or [])]
    for k, x in enumerate(extra):      # the name it has now: a session renamed since it started keeps the new one
        if r.get("name") and x in ("-n", "--name") and k + 1 < len(extra):
            extra[k + 1] = r["name"]
        elif r.get("name") and x.startswith("--name="):
            extra[k] = "--name=" + r["name"]
    return flags + extra


def has_name_flag(r):
    return any(x in ("-n", "--name") or str(x).startswith("--name=") for x in (r.get("flags") or []))


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
        tail = ["--name", shlex.quote(r["name"])] if r.get("name") and not has_name_flag(r) else []
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
    return live_desktop(keep), left


def live_desktop(pairs):
    """An env_file is written while a desktop is open. After a reboot that desktop is gone, and a session
    should not be pointed at a display or a session bus that is no longer there. A local display whose
    socket is missing is left out. A session bus whose socket is missing is replaced by the user's own
    bus when that one exists, and left out otherwise."""
    d, out = dict(pairs), []
    xroot = os.environ.get("TARDIGRADE_FAKE_X11_DIR", "/tmp/.X11-unix") if TESTING else "/tmp/.X11-unix"
    for k, v in pairs:
        if k == "DISPLAY":
            m = re.fullmatch(r":(\d+)(\.\d+)?", v)
            if m and not os.path.exists(os.path.join(xroot, "X" + m.group(1))):
                continue
        if k == "DBUS_SESSION_BUS_ADDRESS" and v.startswith("unix:path="):
            if not os.path.exists(v[len("unix:path="):].split(",")[0]):
                own = os.path.join(d.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "bus")
                if not os.path.exists(own):
                    continue
                v = "unix:path=" + own
        out.append((k, v))
    return out


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
    lines.append("unset " + CHILD_VARS + " " + UNIT_VARS)
    if r.get("config_dir"):
        lines.append(f"export CLAUDE_CONFIG_DIR={shlex.quote(r['config_dir'])}")
    ok = plain_names(CFG.get("carry_env"))
    for k, v in sorted((r.get("carry_env") or {}).items()):
        if k in ok and SAFE_VALUE.fullmatch(str(v)):
            lines.append(f"export {k}={shlex.quote(str(v))}")
    # The account label is set here, by us (G2.41): a wrapper that fetches a login need not put it back, and
    # without it the next record would not know the session is pinned. A session with no label has it unset.
    acct = (plain_names([CFG.get("account_env")]) or [""])[0]
    if acct and r.get("account") and SAFE_VALUE.fullmatch(str(r["account"])):
        lines.append(f"export {acct}={shlex.quote(str(r['account']))}")
    elif acct:
        lines.append(f"unset {acct}")
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
    if not PATH_SET[0]:
        # The default comes first, so claude is found where its installer put it (G2.49). After it, every folder
        # of this process's own PATH that the default lacks: a pane never has less than it would have had. Only
        # whole paths: an empty or relative entry would mean the pane's own folder, the session's project.
        have = e["PATH"].split(":")
        e["PATH"] = ":".join(have + [d for d in dict.fromkeys(os.environ.get("PATH", "").split(":"))
                                     if d.startswith("/") and d not in have])
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


def login_key(r):
    """which login a session uses, as far as a record knows: its account label, its login fingerprint and its
    Claude Code folder. Sessions with the same key share one usage limit (G2.45)."""
    return (str(r.get("account") or ""), login_print(r), str(r.get("config_dir") or ""))


def login_words(r):
    if r.get("account"):
        return f"the account {r['account']}"
    return "the login these sessions share" if login_print(r) else "this machine's own login"


def same_login(r, pid):
    want = login_print(r)
    return want == "unknown" or env_facts(pid)["token_print"] == want


def new_session(sock, name, cwd, cmd):
    os.makedirs(os.path.dirname(sock), mode=0o700, exist_ok=True)
    ensure_server(sock)
    argv = [CFG["tmux"], "-S", sock]
    conf = os.path.expanduser(str(CFG.get("tmux_conf") or ""))
    if conf and os.path.isfile(conf):
        argv += ["-f", conf]         # read only when this call is the one that starts the server
    argv += ["new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", cwd, cmd]
    return run(argv, env=server_env())[0]      # nothing of the caller's environment, and no value on a command line


def start_lane(r, created):
    sock, name = r["tmux_socket"], r["tmux_session"]
    cmd = launch_cmd(r)
    cwd = r["cwd"] if os.path.isdir(r["cwd"]) else HOME
    if (sock, name) in created and has_session(sock, name):
        if is_waiting(sock, name):
            # its turn: Claude replaces the waiting screen in the same pane, through the same start file.
            # The pane gets a new terminal, so nothing typed at the waiting screen reaches Claude.
            # tmux gives a respawned pane the PATH of the client that asks (tmux 3.4): ask with the clean
            # environment, as new_session does, or at boot the pane gets the boot service's PATH (G2.49).
            rc = tmux(sock, "respawn-pane", "-k", "-t", f"={name}:", "-c", cwd, cmd, env=server_env())[0]
            tmux(sock, "set-option", "-u", "-t", f"={name}:", WAIT_MARK)
            tmux(sock, "clear-history", "-t", f"={name}:")
            return rc == 0
        # a retry: the session this run made holds a shell because claude ended
        cur = tmux(sock, "display", "-p", "-t", f"={name}:", "#{pane_current_command}")[1].strip()
        if cur not in SHELLS:
            log(f"  {display(r)}: its tmux session is busy ({cur}); not touching it")
            return False
        rc = tmux(sock, "respawn-pane", "-k", "-t", f"={name}:", "-c", cwd, cmd, env=server_env())[0]
    else:
        rc = new_session(sock, name, cwd, cmd)
        created.add((sock, name))
    return rc == 0


# ---------- the waiting screen ----------
WAIT_MARK = "@tardigrade_waiting"     # a tmux option on a session that holds a waiting screen, not a session


def progress_path():
    return os.path.join(STATE, "progress.json")


def is_waiting(sock, name):
    """Does this tmux session hold one of our waiting screens? The mark is a tmux option on the session
    (never its name), and the pane must still run the waiting program, not Claude and not a shell."""
    if tmux(sock, "show-options", "-qv", "-t", f"={name}:", WAIT_MARK, timeout=6)[1].strip() != "1":
        return False
    out = tmux(sock, "display", "-p", "-t", f"={name}:", "#{pane_pid}", timeout=6)[1].strip()
    a = argv_of(int(out)) if out.isdigit() else []
    return len(a) >= 3 and a[-2] == "wait" and any(x.endswith("tardigrade.py") for x in a)


def show_waiting(todo, created):
    """At the start of a run: each session to be started gets its own tmux session at once, holding the
    waiting screen, so the place where it will appear says from the first minute that it is coming."""
    for sock in sorted({r["tmux_socket"] for r in todo}):
        ensure_server(sock)            # a machine's own server first, and up, before the first waiting screen
    py, script = sys.executable or "python3", os.path.abspath(__file__)
    for r in todo:
        sock, name = r["tmux_socket"], r["tmux_session"]
        if has_session(sock, name):
            if is_waiting(sock, name):     # left by a run that stopped half way: ours, to be used again
                created.add((sock, name))
            continue
        cwd = r["cwd"] if os.path.isdir(r["cwd"]) else HOME
        cmd = f"{shlex.quote(py)} -I {shlex.quote(script)} wait {shlex.quote(str(r['session_id']))}"
        if new_session(sock, name, cwd, cmd) == 0:
            created.add((sock, name))
            tmux(sock, "set-option", "-t", f"={name}:", WAIT_MARK, "1")
            tmux(sock, "select-pane", "-t", f"={name}:", "-T", f"{display(r)} (coming back)")


def holds_text(r):
    """what a session's tmux session holds now, said plainly for the log"""
    if not has_session(r["tmux_socket"], r["tmux_session"]):
        return "is gone"
    if is_waiting(r["tmux_socket"], r["tmux_session"]):
        return "still holds its waiting screen, which now says the restore stopped and to run `tardigrade revive`"
    return "holds a shell"


def end_waiting_screen(sock, name):
    """INV1's one exception for a tmux session: our own waiting screen, when its session turned out to be
    running somewhere else. Only a session that carries the mark and still runs the waiting program."""
    if is_waiting(sock, name):
        tmux(sock, "kill-session", "-t", f"={name}")


def write_progress(p):
    os.makedirs(STATE, exist_ok=True)
    write_json(progress_path(), p)


def wait_screen():
    """The waiting screen: a small program, not a shell. It draws, sleeps and reads nothing it acts on.
    Input is neither echoed nor run; Ctrl+C, hangup and terminate are ignored; no line ends in a prompt
    mark, so a tool that types into idle panes finds nothing here to type into."""
    import select
    import signal
    sid = (ARGS or [""])[0]
    for sg in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGTSTP):
        signal.signal(sg, signal.SIG_IGN)
    fd = sys.stdin.fileno()
    try:
        import termios
        a = termios.tcgetattr(fd)
        a[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
        termios.tcsetattr(fd, termios.TCSANOW, a)
    except Exception:
        pass
    os.set_blocking(fd, False)

    def safe(line):
        return line + " ." if line.rstrip().endswith(("$", "%", "#", "❯", ">")) else line

    while True:
        try:
            while select.select([fd], [], [], 0)[0]:
                if not os.read(fd, 4096):
                    return 0                # the terminal is gone
        except OSError:
            return 0
        pr = read_json(progress_path(), {})
        me = (pr.get("sessions") or {}).get(sid) or {}
        # the run is alive: its process is, it has not finished, and it began after this machine started
        alive_run = (not pr.get("done") and alive(int(pr.get("pid") or 0))
                     and float(pr.get("began") or 0) >= time.time() - uptime_s() - 5)
        lines = ["", "  Windstorm Tardigrade is bringing this session back.", "",
                 f"  Session:  \"{me.get('name') or sid[:8]}\""]
        if me.get("state") == "elsewhere":
            lines += [f"  It is already running somewhere else: {me.get('where', '')}.",
                      "  This screen closes by itself."]
        elif alive_run:
            lines += [f"  Restore:  {pr.get('back', 0)} of {pr.get('total', 0)} sessions are back.",
                      "  It will appear here by itself. Please do not start it by hand:",
                      "  two copies of one session would both act."]
        else:
            lines += ["  The restore that was bringing it back has stopped.",
                      "  To go on, run:  tardigrade revive"]
        lines += ["", "  (This screen takes no input.)"]
        try:
            os.write(sys.stdout.fileno(), ("\x1b[2J\x1b[H" + "\r\n".join(safe(x) for x in lines) + "\r\n").encode())
        except OSError:
            return 0
        time.sleep(1 if TESTING else 3)


def came_up(r, P=None):
    """The Claude process in this session's tmux pane, or None. P: a process list taken a moment ago; on a
    machine with thousands of processes one list per pass is taken and handed down, not one per session."""
    rc, out = tmux(r["tmux_socket"], "list-panes", "-t", f"={r['tmux_session']}", "-F", "#{pane_pid}")
    if rc != 0 or not out.split():
        return None
    c = claude_under(int(out.split()[0]), P or procs())
    return c if c and alive(c) else None


YES_LINE = re.compile(r"❯\s*(\d\.\s*)?Yes, I trust")
# Claude Code's usage and limit notices, in the forms seen and the forms they may take: a key or a note must
# never land beside one (G2.40). Holding back by mistake costs a note; typing by mistake costs a message.
OTHER_DIALOG = re.compile(r"usage[ -]credits|extra[ -]usage|usage limit|limit reached|reached your|hit your|"
                          r"(weekly|daily|hourly|session|5-hour|opus|sonnet|plan'?s?) (usage )?limit|limit resets|"
                          r"resets at|/upgrade|Stop and wait|Wait here|Switch to", re.I)


def bottom(screen, n):
    return "\n".join([ln for ln in screen.splitlines() if ln.strip()][-n:])


def under_box(screen):
    """T36: how many lines with text are under the lowest rule on the screen (the input box's lower edge):
    notices, the status line, which a person can make any height, and the mode line. A fixed count from
    the foot missed the box on a machine whose status line is 13 lines tall. At most 40."""
    lines = [SGR.sub("", ln) for ln in screen.splitlines() if SGR.sub("", ln).strip()]
    for i in range(len(lines) - 1, -1, -1):
        if BOX_EDGE.match(lines[i]):
            return min(len(lines) - 1 - i, 40)
    return 0


def near_box(screen, n):
    """the n lines with text above the status line, and all that is under the box (see under_box)"""
    return bottom(screen, n + under_box(screen))


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
# why a note is held for the rest of the run (G2.45); a notice on the screen may yet go, these do not
HELD_CONV = "its conversation last ended on a usage or limit reply"
HELD_ACCOUNT = "its login is at its usage limit"
HELD_SCREEN = "a usage or limit notice is on its screen"
# what Claude Code shows when a message met a limit and is held for the reset (G2.45)
QUEUED = re.compile(r"continue automatically|continuing automatically", re.I)
# live evidence, on a screen now, that a login is at its limit; "approaching" a limit is not (G2.45)
AT_LIMIT = re.compile(r"limit reached|hit your (weekly |daily |session )?limit|reached your (plan'?s? )?(usage )?limit|"
                      r"continue automatically|continuing automatically", re.I)


def tmp_emptied():
    """Was /tmp emptied by this restart? Only said when it was: nothing in it is older than this boot."""
    d = os.environ.get("TARDIGRADE_FAKE_TMP", "/tmp") if TESTING else "/tmp"
    booted = time.time() - uptime_s()
    try:
        for e in os.scandir(d):
            if e.stat(follow_symlinks=False).st_mtime < booted - 5:
                return False
    except OSError:
        return False
    return True


def note_problem(text):
    """G2.45: the words in a note that Tardigrade would take, on the screen after typing it, for Claude Code's
    usage or limit notice ("" if none). Such a note would hold every other note on its login."""
    for pat in (AT_LIMIT, QUEUED, OTHER_DIALOG):
        m = pat.search(text or "")
        if m:
            return m.group(0)
    return ""


def wake_note(boot):
    """the note's words: the person's own (wake_text), or the default, which says when /tmp was emptied. Words
    that read as a limit notice are refused, and the default is used (G2.45)."""
    if CFG["wake"] != "note" and not CFG.get("wake_names"):
        return ""
    text = str(CFG.get("wake_text") or "") or WAKE_NOTE
    bad = note_problem(text) if CFG.get("wake_text") else ""
    if bad:
        log(f"wake_text is not used: its words \"{bad}\" read as Claude Code's usage or limit notice; the default "
            "note is used instead. Change wake_text in config.json.")
        text = WAKE_NOTE
    if (not CFG.get("wake_text") or bad) and boot and tmp_emptied():
        text += (" This restart emptied /tmp: scripts, tools or files you kept there are gone; make again only "
                 "what you still need, somewhere that lasts.")
    return text.replace("{when}", time.strftime("%Y-%m-%d %H:%M %Z"))


def wants_note(r):
    """G2.37: the note per session. wake_skip patterns never get it; wake_names patterns get it even when
    "wake" is "idle"; otherwise "wake" decides."""
    nm = r.get("name") or ""
    pats = lambda k: [x for x in (CFG.get(k) or []) if isinstance(x, str)]
    if any(re.search(x, nm) for x in pats("wake_skip")):
        return False
    if any(re.search(x, nm) for x in pats("wake_names")):
        return True
    return CFG["wake"] == "note"


BOX_EDGE = re.compile(r"^\s*[─━]{8,}")
SGR = re.compile(r"\x1b\[[0-9;]*m")
DIM = re.compile(r"\x1b\[2m.*?(?=\x1b\[(?:0|22)?m|$)")


def box_empty(screen):
    """Claude Code's input box at the foot of the screen, with nothing typed in it: a line that begins with
    ❯ right under a rule, with nothing after the ❯ but its dim placeholder (a session nobody wrote in shows
    one, such as 'Try "how does <filepath> work?"'). The screen is read with its styles (capture-pane -e),
    so typed text and the placeholder can be told apart. No box found means: not known to be empty."""
    lines = [ln for ln in screen.splitlines() if SGR.sub("", ln).strip()][-(15 + under_box(screen)):]
    for i in range(len(lines) - 1, 0, -1):
        plain = SGR.sub("", lines[i]).lstrip()
        if plain.startswith("❯") and BOX_EDGE.match(SGR.sub("", lines[i - 1])):
            after = lines[i][lines[i].index("❯") + 1:]
            return not SGR.sub("", DIM.sub("", after)).strip()
    return False


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
        self.tabs_done = set()
        self.last_look = time.time()
        self.note_times = []
        self.held = {}                  # session id: why its note is held back, when that is known
        self.gone_new = []              # sessions seen going down in a pass, for the run to look at (G2.43)
        self.queued = []                # sessions whose note met a limit and waits for the reset (G2.45)
        self.at_limit = set()           # logins known to be at their usage limit in this run (G2.45)
        self.conv_read = set()          # sessions whose conversation was looked at for a limit reply
        self.stale_limit = set()        # sessions whose last limit reply has reset since (G2.48)
        self.gone_at = {}               # sid: when the watch saw it go down (G3.17)
        self.after_due = {}             # sid: (when its note was typed, its screen just before, its row) (G2.45)

    def take_gone(self):
        out, self.gone_new = self.gone_new, []
        return out

    def may_note(self):
        """at most notes_per_min notes in any one minute: on a machine with many sessions they are spaced"""
        win = 6 if TESTING else 60
        self.note_times = [t for t in self.note_times if time.time() - t < win]
        return len(self.note_times) < max(1, int(float(CFG.get("notes_per_min") or 6)))

    def login_at_limit(self, r):
        """A usage limit belongs to the login, not to one conversation: once one session shows it LIVE in this
        run (limit words that appeared on its screen after our note; never what was there before, which after a
        resume is its history, and never an old reply in its conversation), no note is
        typed into any other session on the same login for the rest of this run, or every one of them would
        wait in Claude Code's queue and all run at once at the reset (G2.45)."""
        k = login_key(r)
        if k in self.at_limit:
            return
        self.at_limit.add(k)            # each other session on it is held at its own turn, its own reasons first
        log(f"{login_words(r)} is at its usage limit: no more notes are typed into sessions on it in this run, so "
            "they do not all wait in Claude Code's queue and run at once at the reset")

    def tabs(self, last=False):
        """Herdr only: attach each tab not yet done, in every pass, from the start of the run (G4.3). The
        last call waits for Herdr's server if it is not up yet."""
        if not (CFG["herdr"] and CFG["herdr_tabs"]):
            return
        due = [r for r in self.tab_rows if r.get("herdr_tab") and r["session_id"] not in self.tabs_done]
        if not due:
            return
        up = herdr_up()
        if not up:
            herdr_missing(len(due))       # G4.10: said at the first pass, not only after the last wait
        if last or up:
            self.tabs_done |= attach_tabs(due, wait=last)

    def reattach(self, sid):
        self.tabs_done.discard(sid)

    def look(self, started):
        """one pass over the started sessions and the tabs; returns how many sessions are still waited for"""
        self.last_look = time.time()
        self.tabs()
        self.after_looks()
        waiting, P = 0, procs()               # one process list per pass, for every session in it
        if not P.ok:       # T53: cannot tell is not "went down": this pass concludes nothing about any session
            log(f"  {P.why}: this look at the started sessions concludes nothing; the next one reads again")
            return len([r for r in started if r["session_id"] not in self.settled and r["session_id"] not in self.gone])
        for r in started:
            sid = r["session_id"]
            if sid in self.settled or sid in self.gone:
                continue
            pid = came_up(r, P)
            if not pid:
                log(f"  {display(r)}: went down again while being watched")
                self.gone.add(sid)
                self.gone_at[sid] = int(time.time())     # G3.17
                self.gone_new.append(r)
                continue
            sj = session_json(pid, r.get("config_dir", ""))
            if not sj.get("sessionId"):
                if CFG["answer_trust"] and answer_trust(r):
                    self.answered.append(display(r))
                waiting += 1
                continue
            final = (HELD_CONV, HELD_ACCOUNT)
            if sid in self.note_for and sid not in self.noted_ids and self.held.get(sid) not in final:
                # its own reasons first: an old limit reply in its conversation (looked at once), its screen now
                limit, written = ("", 0) if sid in self.conv_read else limit_reply(r)
                self.conv_read.add(sid)
                reset = limit_reset(limit, written) if limit else None
                if limit and reset is not None and time.time() >= reset + LIMIT_MARGIN_S:
                    # G2.48: that limit has reset since; Claude Code holds nothing, and the note just runs
                    self.stale_limit.add(sid)
                    log(f"  {display(r)}: its last limit reply (\"{limit}\") reset at "
                        f"{time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(reset))}; the wake-up note is typed")
                    limit = ""
                if limit:
                    self.held[sid] = HELD_CONV
                    log(f"  {display(r)}: its conversation last ended on a usage or limit reply (\"{limit}\"); the "
                        "wake-up note is not typed: Claude Code would hold it and run it by itself at the reset")
                    # that reply may be days old and the limit long reset: it holds this session's note only
                elif login_key(r) in self.at_limit and self.held.get(sid) != HELD_SCREEN:
                    self.held[sid] = HELD_ACCOUNT
            if sid in self.note_for and sid not in self.noted_ids and self.held.get(sid) not in final \
                    and not (self.held.get(sid) == HELD_SCREEN and login_key(r) in self.at_limit):
                styled = tmux(r["tmux_socket"], "capture-pane", "-e", "-p", "-t", f"={r['tmux_session']}:", timeout=6)[1]
                screen = SGR.sub("", styled)
                # G2.48: a session whose last limit reply has reset shows that reply in its history on the screen
                old = sid in self.stale_limit
                if OTHER_DIALOG.search(near_box(screen, 15)) and not old:
                    self.held[sid] = HELD_SCREEN   # this session only: after a resume, its screen is history
                if sj.get("status") == "idle" and not QUESTION.search(near_box(screen, 15)) and box_empty(styled) \
                        and (old or not OTHER_DIALOG.search(near_box(screen, 15))) and self.may_note() \
                        and login_key(r) not in {login_key(x[2]) for x in self.after_due.values()}:
                    self.note_times.append(time.time())
                    type_note(r, self.note_for[sid])
                    self.noted.append(display(r))
                    self.noted_ids.add(sid)
                    log(f"  {display(r)}: loaded and idle; wake-up note typed")
                    self.after_due[sid] = (time.time(), screen, r)    # looked at once more, in a later pass
                else:
                    waiting += 1
                    continue
            self.settled.add(sid)
        return waiting + len(self.after_due)

    def after_looks(self, all_now=False):
        """A note's second look, a few seconds after it was typed, without holding up the pass: did it meet a
        limit? Only limit words that were not on the screen before the note count (G2.45). Until it is done,
        no other note on the same login is typed."""
        for sid, (t, before, r) in list(self.after_due.items()):
            wait = (1.5 if TESTING else 4) - (time.time() - t)
            if wait > 0 and not all_now:
                continue
            if wait > 0:
                time.sleep(wait)
            del self.after_due[sid]
            after = SGR.sub("", tmux(r["tmux_socket"], "capture-pane", "-p", "-t", f"={r['tmux_session']}:", timeout=6)[1])
            new = lambda pat: bool(pat.search(near_box(after, 15))) and len(pat.findall(after)) > len(pat.findall(before))
            if new(QUEUED):
                self.queued.append(display(r))
                log(f"  {display(r)}: the note met a usage limit; Claude Code holds it and will run it by itself at "
                    "the reset (press Esc in that session to drop it)")
            if new(QUEUED) or new(AT_LIMIT):
                self.login_at_limit(r)

    def count(self, started):
        self.after_looks(all_now=True)
        res = {"trust_answered": self.answered, "noted": self.noted, "loaded": [], "not_loaded": [],
               "down_again": [], "note_not_typed": [], "note_queued": self.queued, "note_held_login_at_limit": []}
        why = {}
        for k in range(4):     # T53: the count says nothing on a failed read; it reads again before it gives up
            P = procs()
            if P.ok or k == 3:
                break
            log(f"  {P.why}: the count reads again in {float(CFG['retry_gap_s']):g} s")
            time.sleep(float(CFG["retry_gap_s"]))
        if not P.ok:
            res["cannot_tell"] = P.why
            log(f"  count: cannot tell which of the {len(started)} started sessions are up ({P.why}); none is "
                "counted as loaded or down")
        for r in (started if P.ok else []):
            pid = came_up(r, P)
            if not pid:
                res["down_again"].append(display(r))
            elif not session_json(pid, r.get("config_dir", "")).get("sessionId"):
                res["not_loaded"].append(display(r))
            else:
                res["loaded"].append(display(r))
                if r["session_id"] in self.note_for and r["session_id"] not in self.noted_ids:
                    res["note_not_typed"].append(display(r))
                    why[display(r)] = self.held.get(r["session_id"], "not idle (a question on its screen?)")
                    if self.held.get(r["session_id"]) == HELD_ACCOUNT:
                        res["note_held_login_at_limit"].append(display(r))
        log(f"count: started={len(started)} LOADED={len(res['loaded'])} UP-NOT-LOADED={len(res['not_loaded'])} "
            f"DOWN={len(res['down_again'])}")
        for nm in res["not_loaded"]:
            log(f"  UP-NOT-LOADED {nm}: Claude Code runs but has not loaded the session (a question on its screen?)")
        for nm in res["down_again"]:
            log(f"  DOWN {nm}: it started and ended again; its tmux session holds a shell")
        for nm in res["note_not_typed"]:
            log(f"  {nm}: loaded, but {why[nm]}; the wake-up note was not typed")
        if self.queued:
            log(f"note queued until the reset: {len(self.queued)} session(s): {', '.join(self.queued)}")
        if res["note_held_login_at_limit"]:
            log(f"note not typed, its login at its usage limit: {len(res['note_held_login_at_limit'])} session(s)")
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
        theirs = lock_not_ours(lock)
        if theirs:     # G3.28: a folder by this name that holds anything but our owner file is not a lock of ours
            log(f"revive: {lock} is there and holds what Tardigrade did not write ({theirs}); it is yours, so it "
                "is not taken or removed, and no session is started. Move it out of this folder, then revive again.")
            return 1
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
        free_lock(lock)
        os.mkdir(lock)
    with open(owner, "w") as f:
        f.write(f"{boot_id()} {os.getpid()}\n")
    try:
        return _revive(boot)
    finally:
        free_lock(lock)


def lock_not_ours(lock):
    """'' when the lock folder is ours (a real folder holding nothing, or only our owner file); else what in it
    is not ours, named"""
    if os.path.islink(lock) or not os.path.isdir(lock):
        return "it is not a folder"
    try:
        other = sorted(set(os.listdir(lock)) - {"owner"})
    except OSError:
        return "it cannot be read"
    if other:
        return ", ".join(other[:5]) + (f" and {len(other) - 5} more" if len(other) > 5 else "")
    o = os.path.join(lock, "owner")
    if not os.path.lexists(o):
        return ""
    try:
        if os.path.islink(o) or not os.path.isfile(o) or os.path.getsize(o) > 200:
            return "owner is not our owner file"
        with open(o) as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return "owner cannot be read"
    # ours says "<boot id> <pid>"; an empty one is ours too (a revive cut off between making it and writing it)
    return "" if re.fullmatch(r"(\S+ \d+\n?)?", text) else "owner is not our owner file"


def free_lock(lock):
    """remove our owner file, then the folder only when it is empty; never anything else in it"""
    if lock_not_ours(lock):
        return
    try:
        os.remove(os.path.join(lock, "owner"))
    except OSError:
        pass
    try:
        os.rmdir(lock)
    except OSError:
        pass


def _revive(boot):
    if boot:      # G3.29: the boot run says it too, in its log, until the person closes the folder
        for line in open_folder_lines():
            log("revive --boot: " + line)
    if not CFG["tmux"]:
        log("revive: tmux is not installed; nothing can be started. Install tmux, then run setup again.")
        return 1
    rows = read_table()["lanes"]
    want = ""
    if "--only" in ARGS and ARGS.index("--only") + 1 < len(ARGS):
        want = ARGS[ARGS.index("--only") + 1]
        rows = [r for r in rows if want in (r["name"], r["session_id"], r["tmux_session"])]
        if not rows:
            log(f"revive --only: no session on the record is named {want!r}")
            return 1
        if len(rows) > 1:
            # A name is not an identity: a copy started by hand carries the real session's name. Guessing
            # which one was meant is how a session its owner had just closed got started again.
            P = procs()
            held = running_sids(P)
            log(f"revive --only: {len(rows)} sessions on the record match {want!r}; started nothing. "
                "Give the session id of the one you mean:")
            for r in rows:
                log(f"  {r['session_id']}  {display(r)}  tmux {r['tmux_session']}  "
                    f"{('running' if r['session_id'] in held else 'not running') if P.ok else 'cannot tell'}  {r['cwd']}")
            return 1
    log(f"=== revive v{VERSION} (boot={boot}, dry-run={DRY}) uptime={int(uptime_s())} s, {len(rows)} to look at")
    up_s = user_services_up_s() if boot else None
    if boot:
        log(f"boot run began {int(uptime_s())} s after boot: "
            + (f"this user's services came up {int(up_s)} s after boot, then " if up_s is not None else "")
            + f"this service's own wait of {int(float(CFG['boot_wait_s']))} s (boot_wait_s)")
    ids = [r["session_id"] for r in rows]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    for i in twice:
        log(f"skip  {i}: the record holds {ids.count(i)} rows with this session id; started none of them. "
            "Two starts of one id would be two copies. Run `record --force` while the session runs, then revive.")
    rows = [r for r in rows if r["session_id"] not in twice]
    # T52: what runs already is read before anything is started. A read that failed is "cannot tell", never
    # "nothing runs": started on it, a session already running elsewhere would run twice. Nothing is started
    # until a read works; after retry_min of failed reads the run ends having started nothing.
    began, up_began = time.time(), uptime_s()
    P, gap = procs(), float(CFG["retry_gap_s"])
    give_up = time.time() + float(CFG["retry_min"]) * 60
    while not P.ok:
        log(f"revive: {P.why}. It cannot tell which sessions are already running, so it starts nothing now"
            + ("." if DRY else f"; it reads again in {gap:g} s"))
        if DRY or time.time() >= give_up:
            log(f"revive: started nothing: {P.why}" + ("" if DRY else f" for {CFG['retry_min']} min")
                + ". The record is kept as it was. Check what runs with your terminal multiplexer (tmux ls) "
                "before starting anything by hand, then run revive again.")
            if not DRY:    # this boot's report says so, and on_done is told (from the review): never last time's
                report = {"started": [], "skipped": [], "failed": [], "needs_you": [], "ended": [], "left_out": [],
                          "cannot_tell": P.why, "at": int(time.time()), "boot": boot_id(), "on_record": len(rows),
                          "began": int(began), "uptime_at_start": int(up_began), "boot_run": boot, "user_up_s": up_s,
                          "boot_wait_s": int(float(CFG["boot_wait_s"]))}
                write_json(os.path.join(STATE, "last-revive.json"), report)
                on_done(report)
            return 1
        time.sleep(gap)
        P = procs()
    held = running_sids(P)
    EXIT_SIDS[:], HISTORY_SAY[:] = [r["session_id"] for r in rows if r["session_id"] not in held
                                    and not in_skip_folder(r.get("cwd", ""))], [True]
    note = wake_note(boot)
    todo, report = [], {"started": [], "skipped": [], "failed": [], "needs_you": [], "ended": [], "left_out": []}
    # Nothing is typed into a session before its login is checked: a session that comes back on another
    # login than it had (possible where sessions on one machine are pinned to accounts) is listed as
    # "needs you" and gets no note, so nothing runs on the wrong account.
    as_if_down = DRY and "--all" in ARGS      # show every start line, as if nothing ran: for checking before a reboot
    for r in rows:
        nm = display(r)
        if in_skip_folder(r.get("cwd", "")):
            # its folder is left alone now: its live process, if any, is not read, so starting it could make a copy.
            # First of all: nothing more of it is read, not even its conversation for an /exit (G1.15; seen with strace).
            log(f"skip  {nm}: its folder is in skip_cwd_prefix; left alone, never started")
            report["left_out"].append(nm)        # not "already running": it is never started at all
            if want:
                return 1
            continue
        if r["session_id"] in held and not as_if_down:
            log(f"skip  {nm}: already running on this machine")
            report["skipped"].append(nm)
            continue
        ex = 0 if as_if_down or want == r["session_id"] else exited_at(r)
        if ex:
            # its owner ended it after the last record saw it running: that is not something to bring back
            log(f"skip  {nm}: its owner ended it with /exit at {time.strftime('%Y-%m-%d %H:%M', time.localtime(ex))}"
                + (f". To bring it back anyway: revive --only {r['session_id']}" if want else ""))
            report["ended"].append(nm)
            if want:
                return 1
            continue
        ep = print_run(r)
        if ep:
            log(f"skip  {nm}: its conversation is a print-mode or SDK run (entrypoint {ep}), not a session a person "
                "sits in; it is never started (G2.47). The next record moves it to the down list.")
            report["left_out"].append(nm)
            if want:
                return 1
            continue
        if has_session(r["tmux_socket"], r["tmux_session"]) and not as_if_down and not is_waiting(
                r["tmux_socket"], r["tmux_session"]):
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
            p = os.path.join(launch_dir(), f)
            if f not in on_record and is_ours(p, "start"):      # G3.28: only start files we wrote
                os.remove(p)
    except OSError:
        pass
    created, came, note_for = set(), [], {}
    pending = {r["session_id"]: [r, 0] for r in todo}
    try:
        os.remove(herdr_wait_path())       # G4.10: what an earlier run found about Herdr is not this run's
    except OSError:
        pass
    prog = {"pid": os.getpid(), "boot": boot_id(), "began": int(time.time()), "uptime_at_start": int(uptime_s()),
            "total": len(todo), "back": 0, "done": False, "boot_run": boot, "user_up_s": up_s,
            "boot_wait_s": int(float(CFG["boot_wait_s"])),
            "sessions": {r["session_id"]: {"name": display(r), "state": "waiting",
                                           "place": f"{r['tmux_socket']}:{r['tmux_session']}"} for r in todo}}
    write_progress(prog)
    show_waiting(todo, created)
    if todo:
        notify(f"Bringing back {len(todo)} session(s). Each one's place says when it is back.")
    ending = []            # waiting screens whose session runs elsewhere: (socket, name, when to end it)

    def end_due(all_now=False):
        for e in list(ending):
            if all_now or time.time() >= e[2]:
                end_waiting_screen(e[0], e[1])
                ending.remove(e)
    deadline = time.time() + float(CFG["retry_min"]) * 60
    watch_s = (float(CFG["watch_min"]) if boot else min(float(CFG["watch_min"]), 3)) * 60
    if note:                   # spaced notes take longer: the watch lasts until each could have its turn
        watch_s = max(watch_s, len(todo) / max(1, int(float(CFG.get("notes_per_min") or 6))) * (6 if TESTING else 60) + 30)
    w = Watch(note_for, rows)
    w.tabs()                   # each tab at once, to its session's waiting screen
    next_try, last_up, tries_done, said = 0.0, time.time(), False, False
    cannot_tell = ""      # T52: why the last pass started nothing, when its read failed

    inflight = {}          # session id -> (row, when it was started): started, not yet seen up
    at_once = max(1, int(float(CFG.get("starts_at_once") or 1)))

    def up(r, pid):
        nonlocal last_up
        sid, nm = r["session_id"], display(r)
        came.append(r)
        last_up = time.time()
        child = ("CLAUDE_CODE_CHILD_SESSION PRESENT (BAD: this session does not save its transcript)"
                 if env_facts(pid)["child"] else "child marker absent")
        if same_login(r, pid):
            log(f"  {nm}: up ({child}" + ("; same login as before)" if login_print(r) else ")"))
            report["started"].append(nm)
            if note and not r.get("fresh") and wants_note(r):
                note_for[sid] = note
        else:
            log(f"needs you  {nm}: it came back on a different login than it had before. "
                "It is idle and nothing was typed. Close it and start it your usual way.")
            report["needs_you"].append(nm)
        pending.pop(sid, None)
        prog["back"] += 1
        prog["sessions"][sid]["state"] = "up"
        write_progress(prog)

    def came_late(sid, r, pid):
        nonlocal last_up
        nm = display(r)
        if nm in report["failed"]:          # given up, then back after all: it counts as back, once
            report["failed"].remove(nm)
            log(f"  {nm}: up (came up after it was given up)")
        else:
            log(f"  {nm}: up (came up late)")
        report["started"].append(nm)
        came.append(r)
        last_up = time.time()
        if note and same_login(r, pid) and not r.get("fresh") and wants_note(r):
            note_for[sid] = note
        del pending[sid]
        prog["back"] += 1
        prog["sessions"][sid]["state"] = "up"
        write_progress(prog)

    def late_ones():
        """G2.44: a start that was not up at its settle look is looked at in every pass, not only at the next
        retry round, so a session that came up a little late is counted and looked after at once."""
        if not pending:
            return
        P = procs()
        for sid, (r, n) in list(pending.items()):
            if n and sid not in inflight:
                pid = came_up(r, P)
                if pid:
                    came_late(sid, r, pid)

    def no_conversation_late():
        """G2.43: a session with no conversation can take longer than settle_s to say so and end (a Mac whose
        start fetches a login first). It was counted up; it went down. If its own pane, in a tmux session this
        run made, says "No conversation found", it is started new once, as G2.30 does, and counted again."""
        for r in w.take_gone():
            sid, nm = r["session_id"], display(r)
            if r.get("fresh") or CFG.get("start_command") or (r["tmux_socket"], r["tmux_session"]) not in created:
                continue
            if "No conversation found" not in tmux(r["tmux_socket"], "capture-pane", "-p", "-t",
                                                   f"={r['tmux_session']}:", timeout=6)[1]:
                continue
            log(f"  {nm}: Claude Code has no conversation for it (it said so only after it was counted up). "
                "Starting it new, with the same name, folder and flags.")
            for k in ("started", "needs_you"):
                if nm in report[k]:
                    report[k].remove(nm)
            if r in came:
                came.remove(r)
            note_for.pop(sid, None)
            w.gone.discard(sid)
            w.gone_at.pop(sid, None)       # started new: not down
            prog["back"] -= 1
            prog["sessions"][sid]["state"] = "starting"
            write_progress(prog)
            r["fresh"] = True
            report.setdefault("fresh", []).append(nm)
            pace()                     # the load gate holds for this start as for every other
            if start_lane(r, created):
                inflight[sid] = (r, time.time())
            else:
                log(f"  {nm}: its new start failed; its tmux session {holds_text(r)}")
                report["failed"].append(nm)

    def settle(wait_all=False):
        """Look at the starts in flight whose settle time is over. The waits overlap; the decisions do not."""
        while inflight:
            for sid, (r, t0) in list(inflight.items()):
                if time.time() < t0 + float(CFG["settle_s"]):
                    continue
                del inflight[sid]
                pid = came_up(r)
                if not pid and not r.get("fresh") and not CFG.get("start_command") and "No conversation found" in tmux(
                        r["tmux_socket"], "capture-pane", "-p", "-t", f"={r['tmux_session']}:", timeout=6)[1]:
                    # Claude Code keeps no conversation for a session nobody ever wrote in. There is nothing to
                    # resume and nothing to lose: it comes back as a new session with the same name, folder and flags.
                    log(f"  {display(r)}: Claude Code has no conversation for it (nobody ever wrote in it). "
                        "Starting it new, with the same name, folder and flags.")
                    r["fresh"] = True
                    report.setdefault("fresh", []).append(display(r))
                    if start_lane(r, created):
                        inflight[sid] = (r, time.time())
                        continue
                if pid:
                    up(r, pid)
                else:
                    pending[sid][1] += 1
                    log(f"  {display(r)}: not running yet (attempt {pending[sid][1]}); will retry")
            # with many sessions the starts take minutes: the ones already up are looked after meanwhile
            if time.time() - w.last_look >= (0 if TESTING else 15):
                late_ones()
                w.look(came)
                no_conversation_late()
            end_due()
            if not wait_all and len(inflight) < at_once:
                return
            if inflight:
                time.sleep(0.2 if TESTING else 1)

    while True:
        if pending and not tries_done and time.time() >= next_try:
            for sid, (r, n) in list(pending.items()):
                if sid in inflight or sid not in pending:
                    continue
                settle()               # at most starts_at_once started and not yet seen up
                nm = display(r)
                # load_max is read again before each start. Its wait comes BEFORE the read: a session started by
                # hand during that wait (up to load_wait_s) is then seen, not started a second time (T52).
                pace()
                P = procs()            # taken fresh for each session, right before the decision to start it
                if not P.ok:
                    # T52: a failed read is "cannot tell": nothing is started in this pass, not this one, not the next
                    cannot_tell = P.why
                    log(f"  {P.why}: cannot tell whether {nm} or any other session is already running, so nothing "
                        f"is started in this pass; it tries again in {float(CFG['retry_gap_s']):g} s")
                    break
                cannot_tell = ""
                late = came_up(r, P) if n else None
                if late:
                    came_late(sid, r, late)
                    continue
                if sid in running_sids(P):
                    there = holder_text(sid, P)
                    log(f"  {nm}: something else started it meanwhile ({there}); left alone")
                    report["skipped"].append(nm)
                    report.setdefault("elsewhere", []).append(f"{nm}: {there}")
                    del pending[sid]
                    prog["sessions"][sid].update(state="elsewhere", where=there)
                    write_progress(prog)
                    if is_waiting(r["tmux_socket"], r["tmux_session"]):
                        ending.append((r["tmux_socket"], r["tmux_session"], time.time() + (1 if TESTING else 10)))
                    continue
                if (r["tmux_socket"], r["tmux_session"]) in created and not has_session(
                        r["tmux_socket"], r["tmux_session"]):
                    w.reattach(sid)    # its waiting screen was closed: a new session, and its tab attached again
                if start_lane(r, created):
                    inflight[sid] = (r, time.time())
                    report.setdefault("first_start", int(time.time()))
                    report["last_start"] = int(time.time())
                    log(f"  {nm}: started ({len(inflight)} of at most {at_once} not yet seen up)")
                else:
                    pending[sid][1] += 1
                    log(f"  {nm}: not running yet (attempt {pending[sid][1]}); will retry")
                time.sleep(float(CFG["stagger_s"]))
            settle(wait_all=True)
            next_try = time.time() + float(CFG["retry_gap_s"])
        if not tries_done and (not pending or time.time() >= deadline):
            tries_done = True
            for sid, (r, n) in pending.items():
                log(f"  {display(r)}: GAVE UP after {n} attempt(s); its tmux session {holds_text(r)}"
                    + (f". Not started at the last pass: {cannot_tell}" if cannot_tell else ""))
                report["failed"].append(display(r))
            report.update(at=int(time.time()), boot=boot_id(), on_record=len(rows), began=prog["began"],
                          uptime_at_start=prog["uptime_at_start"], boot_run=boot, user_up_s=up_s,
                          boot_wait_s=prog["boot_wait_s"])
            write_json(os.path.join(STATE, "last-revive.json"), report)
            if boot:
                write_marker(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
            log(f"revive: {len(report['started'])} started, {len(report['skipped'])} already running, "
                f"{len(report['failed'])} failed, {len(report['needs_you'])} need you"
                + (f", {len(report['left_out'])} never started (print-mode runs, folders left alone)" if report["left_out"] else ""))
        # the sessions that are up are looked after in every pass, also while another start is being retried
        if inflight:
            settle()
        late_ones()
        waiting = w.look(came)
        no_conversation_late()
        waiting += len(inflight)
        if prog.get("starts_done") != (tries_done and not inflight):
            prog["starts_done"] = tries_done and not inflight     # G3.6: from here the record runs again
            write_progress(prog)
        end_due()
        if tries_done and (not waiting or time.time() >= last_up + watch_s):
            break
        if waiting and not said:
            log(f"watching {waiting} session(s) until loaded, for up to {watch_s / 60:g} min")
            said = True
        time.sleep(1 if TESTING else 10)
    end_due(all_now=True)
    down_during = {}

    def went_down(at_count=False):
        """G3.17: each session this run started and that is not running now: down during the restore, with when"""
        P, new = procs(), False
        if not P.ok:       # T53: a failed read says nothing went down
            return False
        for r in came:
            sid = r["session_id"]
            if sid in down_during or came_up(r, P):
                continue
            down_during[sid] = {"name": display(r), "session_id": sid, "at": int(w.gone_at.get(sid) or time.time())}
            new = True
            if not at_count:
                log(f"  {display(r)}: went down while the restore still waits (after the count)")
        if new:
            report["down_during"] = sorted(down_during.values(), key=lambda x: x["at"])
            write_json(os.path.join(STATE, "last-revive.json"), report)
    if came:
        report.update(w.count(came))
        report["counted_at"] = int(time.time())
        write_json(os.path.join(STATE, "last-revive.json"), report)
        went_down(at_count=True)
        AFTER_COUNT[0] = went_down          # looked at again while the run waits for Herdr
    prog.update(done=True, ended=int(time.time()))
    write_progress(prog)
    notify(f"Restore done: {len(report['started'])} session(s) brought back, "
           f"{len(report['failed']) + len(report['needs_you'])} need you")
    w.tabs(last=True)
    if came:
        went_down()
    AFTER_COUNT[0] = None
    report["tabs"] = TAB_LOG
    write_json(os.path.join(STATE, "last-revive.json"), report)
    if want:
        say_tab(rows[0])
    on_done(report)
    return 0


def say_tab(r):
    """G4.8: `revive --only` says what it did about the session's tab"""
    if not r.get("herdr_tab"):
        log(f"revive --only {display(r)}: tab: none on the record for this session")
        return
    if not (CFG["herdr"] and CFG["herdr_tabs"]):
        log(f"revive --only {display(r)}: tab: Herdr tabs are not set up here; nothing done")
        return
    said = [x.split(": ", 1)[1] for x in TAB_LOG["attached"] if x.startswith(display(r) + ": ")]
    left = [x.split(": ", 1)[1] for x in TAB_LOG["not"] if x.startswith(display(r) + ": ")]
    if said:
        log(f"revive --only {display(r)}: tab: " + ("already there" if said[0] == "already there" else f"attached, {said[0]}"))
    elif left:
        log(f"revive --only {display(r)}: tab: not attached: {left[0]}")
    else:
        log(f"revive --only {display(r)}: tab: not attached (Herdr did not answer, or its tmux session is not there)")


def on_done(report):
    """G2.38: with on_done set, that command is run once a restore ends, with the counts, so a person or a
    program is told instead of having to look. The counts are in its {placeholders} and in TARDIGRADE_* variables."""
    cmd = str(CFG.get("on_done") or "")
    if not cmd:
        return
    counts = {"started": len(report.get("started", [])), "loaded": len(report.get("loaded", [])),
              "not_loaded": len(report.get("not_loaded", [])), "down": len(report.get("down_again", [])),
              "failed": len(report.get("failed", [])), "needs_you": len(report.get("needs_you", []))}
    if report.get("cannot_tell"):      # T53: the count could not read the process list: no number stands for it
        counts.update(loaded="unknown", not_loaded="unknown", down="unknown")
    no_herdr = int((report.get("tabs") or {}).get("herdr_not_running") or 0)      # G4.10
    line = cmd.replace("{herdr_tabs_not_attached}", str(no_herdr))
    for k, v in counts.items():
        line = line.replace("{" + k + "}", str(v))
    env = server_env()
    env.update({"TARDIGRADE_" + k.upper(): str(v) for k, v in counts.items()})
    env["TARDIGRADE_HERDR_TABS_NOT_ATTACHED"] = str(no_herdr)
    if report.get("cannot_tell"):
        env["TARDIGRADE_CANNOT_TELL"] = str(report["cannot_tell"])
    rc = run(["/bin/sh", "-c", line], timeout=60, env=env)[0]
    log(f"on_done ran (exit {rc}): " + ", ".join(f"{v} {k.replace('_', ' ').replace('needs', 'need')}"
                                                 for k, v in counts.items())
        + (f"; Herdr is not running: {no_herdr} tab(s) not attached" if no_herdr else ""))


def notify(text):
    """A desktop note, when there is a desktop, and a Herdr notification where Herdr is up; never an error.
    A boot service has no desktop of its own: the env_file's display and bus are used (G2.17, G2.33)."""
    try:
        e = dict(os.environ)
        e.update(dict(env_file_pairs()[0]))
        if MAC:
            run(["osascript", "-e", f'display notification "{text}" with title "Windstorm Tardigrade"'], timeout=5)
        elif shutil.which("notify-send", path=e.get("PATH")) and (e.get("DISPLAY") or e.get("WAYLAND_DISPLAY")):
            run(["notify-send", "Windstorm Tardigrade", text], timeout=5, env=e)
    except Exception:
        pass
    try:
        if CFG["herdr"] and CFG["herdr_tabs"] and herdr_up():
            herdr("notification", "show", "Windstorm Tardigrade", "--body", text, "--sound", "none", timeout=10)
    except Exception:
        pass


def q_target(name):
    """`=name` (tmux: exactly this session), always inside single quotes. Unquoted, zsh takes a word that
    begins with = for the path of a command and refuses the line."""
    return "'=" + name.replace("'", "'\\''") + "'"


TAB_LOG = {"attached": [], "not": []}     # what this run did about each tab, for `report` (G3.8)
HERDR_DOWN = {}                            # G4.10: when this run found Herdr not running, and how many tabs wait
AFTER_COUNT = [None]                       # G3.17: a look for sessions gone, while the run waits after its count


def herdr_wait_path():
    return os.path.join(STATE, "herdr-wait.json")


def herdr_missing(n):
    """G4.10: a restore whose record has tabs says so as soon as it finds Herdr not running, and census can
    read it. Tardigrade never starts Herdr itself: the tabs are attached when Herdr appears."""
    first = not HERDR_DOWN.get("since")
    HERDR_DOWN.update(tabs=n, since=HERDR_DOWN.get("since") or int(time.time()))
    if first:
        log(f"Herdr is not running: {n} tab(s) wait for it (up to {float(CFG['attach_wait_min']):g} minutes after "
            "the sessions have loaded); Tardigrade does not start Herdr")
    try:
        write_json(herdr_wait_path(), {"tabs": n, "since": HERDR_DOWN["since"], "pid": os.getpid(), "boot": boot_id()})
    except OSError:
        pass


def herdr_back():
    """G4.10: Herdr came up during the run: say so, and census stops saying it is not running"""
    if HERDR_DOWN.get("since"):
        log(f"herdr: its server is up now ({ago(time.time() - HERDR_DOWN['since'])} after it was found not running); "
            "attaching the tabs")
        HERDR_DOWN.clear()
    try:
        os.remove(herdr_wait_path())
    except OSError:
        pass


def herdr_wait_now():
    """census (G4.10): {"tabs": N, "running": the restore still waits for it} while this boot's restore found Herdr
    not running and it is still not, else {}"""
    w = read_json(herdr_wait_path(), {})
    if not isinstance(w, dict) or w.get("boot") != boot_id() or not int(w.get("tabs") or 0):
        return {}
    if CFG["herdr"] and CFG["herdr_tabs"] and herdr_up():
        return {}
    return {"tabs": int(w["tabs"]), "since": int(w.get("since") or 0), "running": alive(int(w.get("pid") or 0))}


MADE = {}      # workspace label -> what this run made again: {"ws", "tab", "pane", "used"}


def idle_shell(pane):
    """(the pane holds an idle shell with nothing typed after its prompt, why not)"""
    pi = (herdr("pane", "process-info", "--pane", pane) or {}).get("process_info") or {}
    fg = pi.get("foreground_processes") or []
    if not (len(fg) == 1 and fg[0].get("pid") == pi.get("shell_pid")):
        return False, "it is running something else"
    rc, out = run([CFG["herdr"], "pane", "read", pane, "--source", "visible"], timeout=40)
    last = [ln for ln in out.splitlines() if ln.strip()][-1:] or [""]
    if not re.search(r"[$%#❯]\s*$", last[0]):
        return False, "it has text typed after the prompt"
    return True, ""


def place_in_lost_workspace(r):
    """G4.5, G4.7: a session whose workspace is gone. Its workspace is made again under its old label, once
    per run, not focused. The tab a new workspace comes with is used for the first session placed there,
    if it holds an idle shell within 10 s (its shell may still be starting), so no blank tab is left;
    otherwise a new tab is made and that one is left as it is. Only if the workspace cannot be made: herdr_default_workspace. Returns (pane, what)."""
    lab = r.get("herdr_workspace") or ""
    if lab and lab not in MADE:
        res = herdr("workspace", "create", "--label", lab, "--cwd", r["cwd"] if os.path.isdir(r["cwd"]) else HOME,
                    "--no-focus") or {}
        wid = (res.get("workspace") or {}).get("workspace_id")
        MADE[lab] = {"ws": wid, "tab": (res.get("tab") or {}).get("tab_id"),
                     "pane": (res.get("root_pane") or {}).get("pane_id"), "used": False} if wid else None
        log(f"herdr: workspace '{lab}' was gone; " + ("made again under its old name" if wid else "could NOT be made again"))
    m = MADE.get(lab)
    if m and not m["used"] and m["tab"] and m["pane"]:
        m["used"] = True
        ok, why = idle_shell(m["pane"])
        # T34: the pane was made a moment ago and its shell may still be starting; give it up to 10 s to show
        # an idle prompt before it is called "not blank" (a real Mac left a blank tab when it was looked at at once)
        end = time.time() + 10
        while not ok and time.time() < end:
            time.sleep(0.5)
            ok, why = idle_shell(m["pane"])
        if ok:
            herdr("tab", "rename", m["tab"], r["herdr_tab"])
            return m["pane"], f"tab '{r['herdr_tab']}' in workspace '{lab}', made again"
        log(f"herdr {display(r)}: the first tab of the workspace made again is not blank ({why}); left as it is")
    wid = m["ws"] if m else None
    where_ = f"workspace '{lab}', made again"
    if not wid and CFG.get("herdr_default_workspace"):
        dl = [w for w in (herdr("workspace", "list") or {}).get("workspaces", [])
              if clean_label(w.get("label")) == CFG["herdr_default_workspace"]]
        wid = dl[0]["workspace_id"] if dl else None
        where_ = f"the default workspace '{CFG['herdr_default_workspace']}'"
    if not wid:
        return None, ""
    res = herdr("tab", "create", "--workspace", wid, "--label", r["herdr_tab"], "--no-focus") or {}
    return (res.get("root_pane") or {}).get("pane_id"), f"new tab '{r['herdr_tab']}' in {where_}"


def attach_tabs(rows, wait=True):
    """Herdr only: put each revived session back in the tab that carries its label. The one thing typed
    into a Herdr pane is the `tmux attach` for that session, and only when the pane holds an idle shell
    with nothing typed after its prompt."""
    want = [r for r in rows if r.get("herdr_tab")]
    done = set()           # the sessions whose tab needs nothing more in this run
    if not want or not CFG["herdr"] or not CFG["herdr_tabs"]:
        return done
    end = time.time() + (float(CFG["attach_wait_min"]) * 60 if wait else 0)
    while not herdr_up():
        herdr_missing(len([r for r in want if has_session(r["tmux_socket"], r["tmux_session"])]) or len(want))
        if time.time() > end:
            log("herdr: its server never came up; tabs left as they are")
            TAB_LOG["herdr_not_running"] = HERDR_DOWN.get("tabs", len(want))
            return done
        if AFTER_COUNT[0]:
            AFTER_COUNT[0]()
        time.sleep(1 if TESTING else 30)
    herdr_back()
    pl, tl, wl = herdr("pane", "list"), herdr("tab", "list") or {}, herdr("workspace", "list") or {}
    tabs, pns, typed = tl.get("tabs", []), (pl or {}).get("panes", []), []
    for r in want:
        sock, s = r["tmux_socket"], r["tmux_session"]
        if not has_session(sock, s):
            continue
        att = f"env -u TMUX {shlex.quote(CFG['tmux'])} -S {shlex.quote(sock)} attach -t {q_target(s)}"
        cand = [t for t in tabs if clean_label(t.get("label")) == r["herdr_tab"]]
        ws = [w for w in wl.get("workspaces", []) if clean_label(w.get("label")) == r.get("herdr_workspace")]
        if ws:
            cand = [t for t in cand if t.get("workspace_id") == ws[0]["workspace_id"]] or cand
        if not cand:
            if ws:
                res = herdr("tab", "create", "--workspace", ws[0]["workspace_id"], "--label", r["herdr_tab"], "--no-focus")
                pane, what = ((res or {}).get("root_pane") or {}).get("pane_id"), f"new tab '{r['herdr_tab']}'"
            else:
                pane, what = place_in_lost_workspace(r)
            done.add(r["session_id"])
            if not pane:
                log(f"herdr {display(r)}: no tab '{r['herdr_tab']}', and its workspace '{r.get('herdr_workspace')}' "
                    "is gone and could not be made again; it runs in tmux with no tab")
                TAB_LOG["not"].append(f"{display(r)}: its workspace is gone and could not be made again")
                continue
            time.sleep(0 if TESTING else 2)
            herdr("pane", "run", pane, att)
            typed.append((r, what))
            continue
        tp = [p for p in pns if p.get("tab_id") == cand[0]["tab_id"]]
        if not tp:
            continue
        pid = tp[0]["pane_id"]
        pi = (herdr("pane", "process-info", "--pane", pid) or {}).get("process_info") or {}
        fg = pi.get("foreground_processes") or []
        done.add(r["session_id"])           # from here on, whatever happens, the tab is done for this run
        if any(attach_of(p.get("argv")) == (sock, s) for p in fg):
            TAB_LOG["attached"].append(f"{display(r)}: already there")
            continue
        ok, why = idle_shell(pid)
        if not ok:
            why = why.replace('it is ', 'is ').replace('it has ', 'has ')
            log(f"herdr {display(r)}: its tab {why}; left alone")
            TAB_LOG["not"].append(f"{display(r)}: its tab {why}; left alone")
            continue
        herdr("pane", "run", pid, att)
        typed.append((r, f"tab '{r['herdr_tab']}'"))
    if typed:
        time.sleep(0 if TESTING else 4)      # then look: did a window really attach to the session?
    for r, what in typed:
        n = tmux(r["tmux_socket"], "display", "-p", "-t", f"={r['tmux_session']}:", "#{session_attached}")[1].strip()
        if n.isdigit() and int(n) > 0:
            log(f"herdr {display(r)}: {what} re-attached")
            TAB_LOG["attached"].append(f"{display(r)}: {what}")
        else:
            log(f"herdr {display(r)}: the attach was typed into its {what}, but nothing is attached to the "
                "session yet; look at that tab")
            TAB_LOG["not"].append(f"{display(r)}: typed into its {what}, nothing attached yet")
    return done


# ---------- census ----------
def census_data():
    mine, others, helpers = live(with_launch=False)
    bad = [x.why for x in PROCS_NOW + PANES_NOW if not x.ok]
    if bad:     # T53: a failed read gives no count at all: "down" read off a failed read gets sessions started twice
        return {"cannot_tell": "; ".join(bad), "uptime_s": uptime_s(), "record_not_written": record_not_written()}
    table = read_table()
    live_ids = {r["session_id"] for r in mine}
    held_now = running_sids(PROCS_NOW[0])  # a print-mode run still running is a helper now, not down (T43)
    down = [dict(r, ended="exit") if exited_at(r) else dict(r, ended="print-run") if print_run(r) else r
            for r in table["lanes"] if r["session_id"] not in live_ids and not in_skip_folder(r.get("cwd", ""))
            and r["session_id"] not in held_now]
    coming = [r for r in down if r.get("tmux_session") and is_waiting(r["tmux_socket"], r["tmux_session"])]
    down = [r for r in down if r not in coming]
    going = restore_now()
    stopped = [] if going else coming        # a waiting screen left by a restore that is no longer running
    coming = coming if going else []
    down += [r for r in table["down"] if r["session_id"] not in live_ids and not in_skip_folder(r.get("cwd", ""))]
    # G3.16: a session started new under a new id (Claude Code had no conversation for it) runs in its old tmux
    # session, under its old name. Its old id is not down: the lane is up, and the next record takes the new id.
    taken = {(r.get("tmux_socket"), r.get("tmux_session")): r for r in mine if r.get("tmux_session")}
    replaced = []
    for r in down:
        now_r = taken.get((r.get("tmux_socket"), r.get("tmux_session")))
        if now_r and not r.get("ended") and now_r.get("name") == r.get("name"):
            replaced.append({"name": display(r), "was": r["session_id"], "now": now_r["session_id"]})
    gone_ids = {x["was"] for x in replaced}
    down = [r for r in down if r["session_id"] not in gone_ids]
    # A name is not an identity (G3.7): a name that more than one session carries, running or on the record,
    # and a session started by hand, under the name of one the last restore was bringing back, after it began.
    ids_by_name = {}
    for r in mine + table["lanes"] + table["down"]:
        if r.get("name") and r["session_id"] not in gone_ids:
            ids_by_name.setdefault(r["name"], set()).add(r["session_id"])
    same_name = {n: sorted(i) for n, i in ids_by_name.items() if len(i) > 1}
    pr = read_json(progress_path(), {})
    # By hand means: the name of one coming back, but none of their ids, and not in a tmux session the restore
    # itself made (it starts a session new there, under a new id, when Claude Code has no conversation for it).
    # Two sessions can share a name, so a name maps to all the ids coming back under it.
    back = (pr.get("sessions") or {}) if isinstance(pr, dict) else {}
    coming_names, places = {}, {v.get("place") for v in back.values() if isinstance(v, dict)}
    for k, v in back.items():
        if isinstance(v, dict):
            coming_names.setdefault(v.get("name"), []).append(k)
    for r in mine:
        sids = coming_names.get(r["name"]) or []
        if sids and r["session_id"] not in sids and f"{r.get('tmux_socket')}:{r.get('tmux_session')}" not in places \
                and r.get("started_at", 0) >= int(pr.get("began") or 0) > 0:
            r["by_hand"] = sids[0]
    on_record = {r["session_id"] for r in table["lanes"]}
    rec_at = int(table.get("recorded_at") or 0)
    not_kept = []
    for r in mine:
        why = left_out_why(r)
        if not why and r["session_id"] not in on_record and r.get("status") != "starting" \
                and r["session_id"] not in {x["now"] for x in replaced}:     # its old row brings it back (G3.16)
            why = ("started after the last record: the next record takes it" if r.get("started_at", 0) >= rec_at
                   else "not on the record: run `tardigrade record`")
        if why:
            not_kept.append({"name": display(r), "session_id": r["session_id"], "why": why})
    hidden = [r for r in mine if not r["window"]]
    shown = [r for r in mine if r["window"]]
    return {
        "at": int(time.time()), "uptime_s": int(uptime_s()), "running": len(mine), "with_window": len(shown),
        "hidden": len(hidden), "down": len(down), "other_users": others, "helpers": helpers,
        "left_alone": LEFT_ALONE[0],
        "waiting": len([r for r in mine if r["status"] in ("waiting", "trust?")]),
        "trust": len([r for r in mine if r["status"] == "trust?"]),
        "lost": len([r for r in down if r.get("lost_since") and not r.get("ended")]), "comes_back": comes_back(),
        "record_not_written": record_not_written(),
        "ended": len([r for r in down if r.get("ended")]),
        "auto_revive": bool(CFG["auto_revive"]), "recorded_at": table.get("recorded_at", 0),
        "on_record": len(table["lanes"]), "last_revive": read_json(os.path.join(STATE, "last-revive.json"), {}),
        "not_kept": not_kept, "replaced": replaced, "coming_back": len(coming), "waiting_stopped": len(stopped), "restore": going, "same_name": same_name,
        "herdr_not_running": herdr_wait_now(),
        "down_during_restore": down_during_restore(live_ids),
        "by_hand": [{"name": r["name"], "session_id": r["session_id"], "in_place_of": r["by_hand"]}
                    for r in mine if r.get("by_hand")],
        "sessions": {"hidden": hidden, "with_window": shown, "down": down, "coming_back": coming,
                     "waiting_stopped": stopped},
    }


def down_during_restore(live_ids):
    """G3.17: the sessions this boot's restore started that went down during it and are not running now"""
    lr = read_json(os.path.join(STATE, "last-revive.json"), {})
    if not isinstance(lr, dict) or lr.get("boot") != boot_id():
        return []
    return [x for x in lr.get("down_during") or [] if isinstance(x, dict) and x.get("session_id") not in live_ids]


def clock(t):
    return time.strftime("%H:%M", time.localtime(t))


def restore_now():
    """the restore running now, from its own progress file, or {} when none is"""
    pr = read_json(progress_path(), {})
    if not isinstance(pr, dict) or pr.get("done") or pr.get("boot") != boot_id() or not alive(int(pr.get("pid") or 0)):
        return {}
    return {k: pr.get(k) for k in ("began", "total", "back", "uptime_at_start", "boot_run", "user_up_s", "boot_wait_s",
                                   "starts_done")}


def began_text(d):
    """how long after boot a boot run began, and what it waited for (G5.8)"""
    if not d.get("boot_run"):
        return ""
    up = d.get("user_up_s")
    return (f"began {ago(d.get('uptime_at_start') or 0)} after boot: "
            + (f"this user's services came up {ago(up)} after boot, then " if up is not None else "")
            + f"its own wait of {d.get('boot_wait_s')} s")


def where_text(r):
    if r["host"] == "tmux":
        return f"tmux {os.path.basename(r['tmux_socket'])}:{r['tmux_session']}"
    return {"herdr": "Herdr", "terminal": "a terminal window", "ide": "an editor", "ssh": "an ssh login",
            "none": "no terminal at all"}.get(r["host"], r["host"])


def record_not_written():
    """the record skipped on failed reads for an hour or more: {since, why}, else None"""
    sk = read_json(SKIP_PATH, {})
    if isinstance(sk, dict) and sk.get("since") and time.time() - sk["since"] >= 3600:
        return {"since": sk["since"], "why": sk.get("why", "")}
    return None


def record_not_written_text(x):
    return (f"RECORD NOT WRITTEN since {time.strftime('%Y-%m-%d %H:%M', time.localtime(x['since']))} "
            f"({ago(time.time() - x['since'])}): {x['why']}. A restart now brings back the sessions recorded before then.")


def cannot_tell_text(why):
    return (f"Cannot tell which sessions run or which are down ({why}). Nothing here says a session is down: "
            "do not start any by hand on the strength of this. Check with your terminal multiplexer (tmux ls), "
            "then run census again.")


def census():
    d = census_data()
    folders = open_folder_lines()
    d["open_folders"] = folders[1:]
    if d.get("cannot_tell"):
        if "--json" in ARGS:
            print(json.dumps(d, indent=1, sort_keys=True))
        else:
            print(f"Windstorm Tardigrade census, {time.strftime('%Y-%m-%d %H:%M %Z')}")
            print(cannot_tell_text(d["cannot_tell"]))
            if d.get("record_not_written"):
                print(record_not_written_text(d["record_not_written"]))
            for line in folders:
                print(line)
        return 1
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
    for line in folders:
        print(line)
    if d.get("record_not_written"):
        print(record_not_written_text(d["record_not_written"]))
    pr = d["restore"]
    if pr:
        print(f"A RESTORE IS RUNNING: {pr['back']} of {pr['total']} sessions back, since "
              f"{time.strftime('%H:%M', time.localtime(pr['began']))} ({ago(now - pr['began'])}). "
              + ("The others are on their way: do not start them by hand." if pr["back"] < pr["total"] and not pr.get("starts_done") else
                 "All are back; it is still watching them load (questions, notes, tabs). Every start is done."
                 if pr["back"] >= pr["total"] else
                 "Every start is done; the others did not come back (see `tardigrade report`). It is still watching "
                 "the ones that did load (questions, notes, tabs)."))
        if began_text(pr):
            print(f"  This boot's restore {began_text(pr)}.")
    for x in d["down_during_restore"]:
        print(f"Down during the restore: {x['name']} (at {clock(x['at'])}): it started, then went down; it may have "
              "failed to load. `tardigrade report` has the restore's account.")
    hw = d["herdr_not_running"]
    if hw:
        print(f"Herdr is not running: {hw['tabs']} tab(s) not attached"
              + ("; the restore attaches them when Herdr appears (Tardigrade does not start it)." if hw["running"] else
                 ". Tardigrade does not start Herdr; start it, then `tardigrade revive --only NAME` attaches one."))
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
                how = {"exit": "   ended by its owner (/exit)",
                       "shell": "   ended (its pane holds the shell it left)",
                       "print-run": "   a print-mode run (claude -p), never brought back"}.get(r.get("ended"), "")
                print(f"  {display(r):<34} last seen {ago(now - t)} ago   {r.get('cwd', '')}{how}")
            else:
                q = ago(now - r["updated_at"]) if r["updated_at"] else "?"
                mark = "   STARTED BY HAND while its session was coming back" if r.get("by_hand") else ""
                print(f"  {display(r):<34} {r['status'] or '?':<8} quiet {q:<8} {where_text(r):<30} {r['cwd']}{mark}")

    if stopped := d["sessions"]["waiting_stopped"]:
        print(f"\nWAITING, STOPPED: a waiting screen holds its place, but no restore is running ({len(stopped)})."
              " Run `tardigrade revive` to bring them back.")
        for r in sorted(stopped, key=lambda x: display(x).lower()):
            print(f"  {display(r):<34} tmux {os.path.basename(r['tmux_socket'])}:{r['tmux_session']}")
    if coming := d["sessions"]["coming_back"]:
        print(f"\nCOMING BACK: a waiting screen holds its place ({len(coming)})")
        for r in sorted(coming, key=lambda x: display(x).lower()):
            print(f"  {display(r):<34} tmux {os.path.basename(r['tmux_socket'])}:{r['tmux_session']}")
    table("HIDDEN: running, but no window shows them", d["sessions"]["hidden"])
    table("WITH A WINDOW", d["sessions"]["with_window"])
    table("DOWN: on the record, not running now", d["sessions"]["down"], down=True)
    if d["replaced"]:
        print(f"\nSTARTED NEW under a new id, in their own place (up, not down; the next record takes the new id): "
              f"{len(d['replaced'])}")
        for x in d["replaced"]:
            print(f"  {x['name']:<34} was {x['was'][:8]}, now {x['now'][:8]}")
    if d["lost"]:
        print(f"\n{d['lost']} of the sessions under DOWN vanished together, which looks like a crash. They are still "
              f"on the record: `tardigrade revive` brings them back. If you ended them yourself: record --force")
    if d["same_name"]:
        print("\nSAME NAME: more than one session carries it. A name is not enough to tell them apart; use the id.")
        for nm, ids in sorted(d["same_name"].items()):
            print(f"  {nm:<34} {len(ids)} sessions: {', '.join(ids)}")
    for b in d["by_hand"]:
        print(f"\n{b['name']} ({b['session_id'][:8]}) was started by hand while the restore was bringing back "
              f"{b['in_place_of'][:8]} under that name. Two sessions may now do one job: end the one you do not want.")
    if d["other_users"]:
        print(f"\nOther users on this machine: {d['other_users']} Claude process(es). Not yours; counted by program name only.")
    if d["helpers"]:
        print(f"Helper sessions started by other sessions: {d['helpers']} (not brought back after a reboot).")
    if d.get("left_alone"):
        print(f"Left alone in a skip_cwd_prefix folder: {d['left_alone']} Claude process(es). Only their folder read; not brought back.")
    if d["not_kept"]:
        print(f"\nRUNNING, BUT WILL NOT COME BACK after a reboot ({len(d['not_kept'])}):")
        for x in sorted(d["not_kept"], key=lambda x: x["name"].lower()):
            print(f"  {x['name']:<34} {x['why']}")
    if d["trust"]:
        print(f"\ntrust? = Claude Code asks whether to trust that session's folder, and the session has not loaded yet "
              f"({d['trust']}). A session in your home folder is asked on every start. Open its window and answer, "
              f"or have it answered after a restart: tardigrade trust on")
    if d["hidden"]:
        print("\nTo open a window for each hidden one: tardigrade open")
    return 0


def report_cmd():
    """G3.8: the last restore's timeline, from the times the run itself wrote down, and what still needs a
    person. Reads only."""
    rep = read_json(os.path.join(STATE, "last-revive.json"), {})
    if not rep or not rep.get("began"):
        print("No restore has run yet with this version (nothing in last-revive.json).")
        return 1

    def at(t):
        return time.strftime("%a %H:%M:%S", time.localtime(t)) if t else "-"

    began, ups = rep["began"], rep.get("uptime_at_start") or 0
    booted = began - ups
    print(f"Windstorm Tardigrade: the last restore ({'after a boot' if rep.get('boot_run') else 'by hand'}), "
          f"times are this machine's ({time.strftime('%Z')})")
    if rep.get("boot_run"):
        print(f"  machine started       {at(booted)}")
        if rep.get("user_up_s") is not None:
            print(f"  user services up      {at(booted + rep['user_up_s'])}   ({ago(rep['user_up_s'])} after boot)")
    print(f"  run began             {at(began)}" + (f"   ({began_text(rep)})" if rep.get("boot_run") else ""))
    print(f"  first start           {at(rep.get('first_start'))}")
    print(f"  last start            {at(rep.get('last_start'))}")
    if rep.get("cannot_tell") and not rep.get("counted_at"):
        # T53: a run that gave up was not counted; zeros here would read like a count
        print(f"  counted               -   not counted: it started nothing, as it could not tell which sessions "
              f"already run ({rep['cannot_tell']})")
    elif rep.get("cannot_tell"):
        print(f"  counted               {at(rep.get('counted_at'))}   {len(rep.get('started', []))} started; loaded, not "
              f"loaded and down again not counted: it could not tell ({rep['cannot_tell']})")
    else:
        print(f"  counted               {at(rep.get('counted_at'))}   {len(rep.get('started', []))} started, "
              f"{len(rep.get('loaded', []))} loaded, {len(rep.get('not_loaded', []))} not loaded, "
              f"{len(rep.get('down_again', []))} down again, {len(rep.get('skipped', []))} already running"
              + (f", {len(rep['left_out'])} never started (print-mode runs, folders left alone)" if rep.get("left_out") else "")
              + (f"   ({ago(rep['counted_at'] - booted)} after boot)" if rep.get("boot_run") and rep.get("counted_at") else ""))
    tabs = rep.get("tabs") or {}
    if tabs.get("attached") or tabs.get("not"):
        print(f"  tabs                  {len(tabs.get('attached', []))} attached, {len(tabs.get('not', []))} not")
    if tabs.get("herdr_not_running"):
        print(f"  tabs                  Herdr was not running: {tabs['herdr_not_running']} tab(s) not attached")
    d = census_data()
    if d.get("cannot_tell"):
        print("\n" + cannot_tell_text(d["cannot_tell"]))
        d = {"same_name": {}, "by_hand": []}
    need = [(("count not taken: cannot tell which sessions are up" if rep.get("counted_at") else
              "nothing was started: it could not tell which sessions already run"),
             [rep["cannot_tell"]] if rep.get("cannot_tell") else []),
            ("failed to start", rep.get("failed")), ("came back on another login (needs you)", rep.get("needs_you")),
            ("not loaded (a question on its screen?)", rep.get("not_loaded")),
            ("loaded, note not typed", rep.get("note_not_typed")),
            ("note waiting in Claude Code's queue for the limit to reset", rep.get("note_queued")),
            ("note not typed (its login is at its usage limit)", rep.get("note_held_login_at_limit")),
            ("down during the restore", [f"{x['name']} (at {clock(x['at'])})" for x in rep.get("down_during") or []]
             if "down_during" in rep else rep.get("down_again")),
            ("ended by its owner, not brought back", rep.get("ended")),
            ("running elsewhere by its turn", rep.get("elsewhere")), ("tab not attached", tabs.get("not")),
            ("a name carried by more than one session", [f"{n}: {', '.join(i)}" for n, i in d["same_name"].items()]),
            ("started by hand while its session was coming back", [f"{b['name']} ({b['session_id'][:8]})" for b in d["by_hand"]])]
    need = [(k, v) for k, v in need if v]
    print("\nStill needs a person:" + ("" if need else " nothing."))
    for k, v in need:
        print(f"  {k}:")
        for x in v:
            print(f"    {x}")
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
        line = " ".join(shlex.quote(x) for x in att[:-1]) + " " + q_target(r["tmux_session"])   # safe to paste in zsh too
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
ExecStartPre=/bin/sleep {int(float(CFG["boot_wait_s"]))}
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
    bad = note_problem(str(CFG.get("wake_text") or ""))
    if bad:
        print(f"wake_text is refused: its words \"{bad}\" read as Claude Code's usage or limit notice, and Tardigrade "
              "would take its own note for one. The default note is used until you change wake_text in config.json.")
    theirs = [p for p, kind in our_places(script) if os.path.lexists(p) and not is_ours(p, kind)
              and os.path.realpath(p) != os.path.realpath(os.path.abspath(__file__))]
    if theirs:      # G3.28: setup is where a person's file stops, before any write
        print("NOT set up: Tardigrade did not write these, so they are yours, and setup does not write over them:")
        for p in theirs:
            print(f"  {p}")
        print("Nothing was written. Move them out of the way, then run setup again.")
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
                ("ai.thewindstorm.tardigrade-revive", f"sleep {int(float(CFG['boot_wait_s']))}; exec",
                 "<key>RunAtLoad</key><true/>"),
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
    # an earlier setup under a wide-open umask left these writable by others: close what is ours (G3.29)
    for p, kind in [(INSTALL_DIR, "")] + our_places(script):
        try:
            if os.path.islink(p) or not (os.path.isdir(p) if not kind else is_ours(p, kind)):
                continue
            m = os.stat(p).st_mode & 0o7777
            if m & 0o022:
                os.chmod(p, m & ~0o022)
        except OSError:
            pass
    save_cfg_key("auto_revive", True)
    # this boot counts as revived: the sessions running now are the ones to remember
    write_marker("setup " + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
    # T35: a plain record, not a forced one: sessions that vanished are held as the timer's record holds them,
    # so installing or upgrading on a machine whose sessions are down forgets none of them
    HERDR_WAIT["s"] = 120          # setup's first record: give a loaded machine's Herdr time to answer
    record()
    print("\nWindstorm Tardigrade is set up. After a restart, your sessions come back by themselves.")
    for line in who_logs_in():
        print(line)
    for line in open_folder_lines():
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


def our_places(script):
    """(path, kind) of every file setup writes outside the state and settings folders"""
    if MAC:
        la = os.path.join(HOME, "Library", "LaunchAgents")
        units_at = [(os.path.join(la, label + ".plist"), "plist") for label in
                    ("ai.thewindstorm.tardigrade-revive", "ai.thewindstorm.tardigrade-record")]
    else:
        ud = os.path.join(HOME, ".config", "systemd", "user")
        units_at = [(os.path.join(ud, name), "unit") for name in units("", "")]
    return [(script, "copy")] + units_at


def group_accounts(gid):
    """every account in group gid: its member list, and every account whose primary group it is (raises when it
    cannot be read)"""
    import grp
    import pwd          # here, not at the top: neither exists on Windows, where setup says it is not supported
    return set(grp.getgrgid(gid).gr_mem) | {u.pw_name for u in pwd.getpwall() if u.pw_gid == gid}


def account_ids(name):
    """(uid, the set of its group ids) of the account name (raises when it cannot be read)"""
    import pwd
    u = pwd.getpwnam(name)
    return u.pw_uid, set(os.getgrouplist(name, u.pw_gid))


def may(st, uid, gids, bit):
    """whether the account (uid, gids) has bit (4 read, 2 write, 1 pass) on a file with this stat"""
    if st.st_uid == uid:
        return bool(st.st_mode >> 6 & bit)
    if st.st_gid in gids:
        return bool(st.st_mode >> 3 & bit)
    return bool(st.st_mode & bit)


def who_can_write(d):
    """Another account that can really REACH and WRITE the folder d (family rule 28, WHEN A PERSON'S FOLDER IS
    NAMED): '' for none, None when it could not be read, else who. Write: writable by others, or by its group when
    that group has a member other than the owner, or owned by another account. Reach: every folder from / down to
    d lets that same account pass. (G3.29)"""
    chain, x = [], d
    while True:
        chain.append(x)
        if x == os.path.dirname(x):
            break
        x = os.path.dirname(x)
    try:
        sts = [os.stat(x) for x in reversed(chain)]
    except OSError:
        return None
    st, me = sts[-1], os.getuid()
    cands = []          # (who, uid, gids)
    if st.st_uid not in (me, 0):
        cands.append(("the account that owns it", st.st_uid, set()))
    if st.st_mode & 0o002:
        cands.append(("every account on this computer", -1, set()))
    if st.st_mode & 0o020:
        try:
            import pwd
            mine = pwd.getpwuid(me).pw_name
            names = sorted(group_accounts(st.st_gid) - {mine})
            for n in names:
                uid, gids = account_ids(n)
                cands.append((f"the account {n} (in the folder's group)", uid, gids))
        except (KeyError, OSError):
            return None
    for who, uid, gids in cands:
        if uid == me:
            continue
        if all(may(s, uid, gids, 1) for s in sts) and may(st, uid, gids, 2):
            return who
    return ""


def open_folders():
    """[(folder, who)] for every folder from the home down to each thing of ours that runs (the installed copy, the
    units or plists) that another account can write, or that could not be read (who None)"""
    seen, out = set(), []
    for p, _ in our_places(os.path.join(INSTALL_DIR, "tardigrade.py")):
        if not os.path.lexists(p):
            continue
        chain, d = [], os.path.dirname(p)
        while True:
            chain.append(d)
            if d == HOME or d == os.path.dirname(d):
                break
            d = os.path.dirname(d)
        for d in reversed(chain):
            if d not in seen:
                seen.add(d)
                who = who_can_write(d)
                if who != "":
                    out.append((d, who))
    return out


def open_folder_lines():
    """the numbered lines for the person, one per open folder, each with the one command that closes it"""
    lines = []
    for i, (d, who) in enumerate(open_folders(), 1):
        if who is None:
            lines.append(f"{i}. {d}: who can write it could not be read (the folder, a folder above it, or its group's "
                         "members), so Tardigrade cannot tell whether another account can replace what is in it.")
        elif who == "the account that owns it":
            lines.append(f"{i}. {d} belongs to another account: that account can replace anything in it, Tardigrade's "
                         "own units and copy included. Only that account or an administrator can close it.")
        else:
            lines.append(f"{i}. {d} can be written by {who}: anything in it, Tardigrade's own units and copy included, "
                         f"can be replaced by another account. To close it: chmod go-w {shlex.quote(d)}")
    if lines:
        lines.insert(0, "On the way to what runs at boot, another account can write these folders (Tardigrade does not "
                        "change a folder it cannot know it made):")
    return lines


def remove_ours(path, kind, kept, failed):
    """remove path when it is ours; a person's file there is kept and named (G3.28)"""
    if not os.path.lexists(path):
        return
    if not is_ours(path, kind):
        kept.append(path)
        return
    try:
        os.remove(path)
    except OSError as e:
        failed.append(f"{path}: {e.strerror}")


def uninstall():
    if DRY:
        print("would remove the boot service, the record timer and the installed copy; the record and the log stay")
        return 0
    # A person's file in one of our folders never stops the leave (family rule 8): everything of ours goes, what
    # is theirs stays and is named, with why each folder stayed, and the exit code is success.
    kept, failed = [], []
    script = os.path.join(INSTALL_DIR, "tardigrade.py")
    places = our_places(script)
    if MAC:
        for label in ("ai.thewindstorm.tardigrade-revive", "ai.thewindstorm.tardigrade-record"):
            run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"])
        for p, kind in places[1:]:
            remove_ours(p, kind, kept, failed)
    else:
        env = user_env()
        run(["systemctl", "--user", "disable", "--now", "windstorm-tardigrade-record.timer"], env=env)
        run(["systemctl", "--user", "disable", "windstorm-tardigrade-revive.service"], env=env)
        for p, kind in places[1:]:
            remove_ours(p, kind, kept, failed)
        run(["systemctl", "--user", "daemon-reload"], env=env)
    remove_ours(script, "copy", kept, failed)
    cache = os.path.join(INSTALL_DIR, "__pycache__")
    try:
        names = [] if os.path.islink(cache) or not os.path.isdir(cache) else sorted(os.listdir(cache))
    except OSError:
        names = []
    for f in names:            # Python's cache of our script only; anything else in there is the person's
        remove_ours(os.path.join(cache, f), "cache", kept, failed)
    stayed = []                # (folder, why) for each of our folders that is still there
    for d in (cache, INSTALL_DIR):
        if os.path.islink(d) or not os.path.isdir(d):
            continue
        try:
            os.rmdir(d)        # only when nothing is left in it
        except OSError:
            try:
                inside = sorted(os.listdir(d))
            except OSError:
                inside = []
            stayed.append((d, "it holds " + (", ".join(inside[:5]) + (f" and {len(inside) - 5} more" if len(inside) > 5 else "")
                                             if inside else "something that could not be read")))
    save_cfg_key("auto_revive", False)
    print("Tardigrade's timer and boot service are off, and its units and installed copy are removed.")
    if kept:
        print("Kept, because Tardigrade did not write them (they are yours, and they stay as they are):")
        for p in kept:
            print(f"  {p}")
    for d, why in stayed:
        print(f"Kept the folder {d}: {why}.")
    print(f"Kept: your record and log in {STATE}, and your settings in {CFG_DIR}; running sessions were not touched.")
    if failed:
        print("These are Tardigrade's, but they could not be removed:")
        for x in failed:
            print(f"  {x}")
        print("Tardigrade's timer and boot service are off, but not all of its files are gone: they are named above.")
        return 1
    print("Tardigrade is off this computer: nothing comes back by itself any more. What stayed is named above.")
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


COMMANDS = {"wait": wait_screen, "report": report_cmd, "trust": trust_cmd, "record": record, "revive": revive, "census": census, "status": census, "open": open_windows,
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
    # G3.29 (T80), family rule 28: nothing we make has a group or other write bit, whatever umask we were started
    # with (a job runner on a shared machine was seen starting every job with umask 000)
    os.umask(os.umask(0o022) | 0o022)
    private_dirs()
    load_cfg()
    sys.exit(COMMANDS[cmd]() or 0)
