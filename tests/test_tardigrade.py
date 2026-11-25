"""Tests for Windstorm Tardigrade. Each test is named after the codon it proves (DNA-STRAND-MASTER-PLAN.md).

They run a stand-in `claude` (tests/fake_claude.py) inside a private tmux folder and a private HOME, so no
real session, no real tmux server and no service is touched. Linux only. Run: python3 -m unittest -v
"""
import ast
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

# THE RUN'S OWN HOME (T78, the guard's first part): before anything here loads the tool, the whole run gets a
# temporary home of its own and loses this account's lines to its desktop session and its tmux server; every program
# the run starts inherits that. git finds no global config there (GIT_CONFIG_NOSYSTEM beside it: older git has no
# GIT_CONFIG_GLOBAL, so the home is the base). Each test still makes its own home under its own folder (Base.setUp).
# THE GUARD'S OTHER PARTS (T87, G5.18; the first is above). The real home is read from the account, before HOME is
# changed; a setting that would put the suite's temporary folders inside it stops the run before anything is made.
try:
    import pwd
    REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
except (ImportError, KeyError):
    REAL_HOME = os.path.realpath(os.path.expanduser("~"))
TMP_ROOT = os.path.realpath(os.environ.get("TARDIGRADE_TEST_TMP", "/tmp"))
if REAL_HOME != os.sep and (TMP_ROOT + os.sep).startswith(REAL_HOME + os.sep):
    sys.exit("The suite stopped before making anything: its temporary folders (TARDIGRADE_TEST_TMP) would be inside "
             "the home of the account that runs it. Nothing was made or changed.")
REAL_TMUX_DIRS = sorted({os.path.join(os.path.realpath(_d), f"tmux-{os.getuid()}")
                         for _d in ("/tmp", os.environ.get("TMUX_TMPDIR") or "/tmp")})
PROGRAMS = ("tmux", "systemctl", "crontab", "loginctl", "launchctl", "claude")


def _real_program(name):
    """this machine's own program of that name: the first on PATH that is not a loud stand-in of this suite (a run
    of the suite started by a test of the suite inherits a PATH with the outer run's stand-ins first)"""
    for d in (os.environ.get("PATH") or os.defpath).split(os.pathsep):
        cand = os.path.join(d or ".", name)
        if not (os.path.isfile(cand) and os.access(cand, os.X_OK)):
            continue
        try:
            with open(cand, "rb") as f:
                if b"the suite's guard: " in f.read(400):
                    continue
        except OSError:
            continue
        return cand
    return None


_REAL_TMUX = _real_program("tmux")      # read before this run's stand-ins come first on PATH
RUN_HOME = tempfile.mkdtemp(prefix="tgrun", dir=os.environ.get("TARDIGRADE_TEST_TMP", "/tmp"))
os.makedirs(os.path.join(RUN_HOME, "home"))
os.environ["HOME"] = os.path.join(RUN_HOME, "home")
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
for _k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "TMUX", "TMUX_PANE",
           "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
    os.environ.pop(_k, None)
# Part 2: what answers for the ACCOUNT and not for its home resolves first to a stand-in that fails loudly and
# writes down that it was reached; a test brings its own. The one real program the tests need, tmux, is linked
# into the run's folder and used by that path, always with a socket folder of the test's own.
STUBS = os.path.join(RUN_HOME, "stand-ins")
UNPLANNED = os.path.join(RUN_HOME, "unplanned.log")
REAL_LINKS = os.path.join(RUN_HOME, "real")
for _d in (STUBS, REAL_LINKS, os.path.join(RUN_HOME, "tmux")):
    os.makedirs(_d)
for _n in PROGRAMS:
    with open(os.path.join(STUBS, _n), "w") as _f:
        _f.write(f'#!/bin/sh\necho "{_n} $*" >> "{UNPLANNED}"\n'
                 f'echo "the suite\'s guard: {_n} was reached and the test brought no {_n} of its own ({_n} $*)" >&2\n'
                 "exit 97\n")
    os.chmod(os.path.join(STUBS, _n), 0o755)
os.environ["PATH"] = STUBS + os.pathsep + os.environ.get("PATH", os.defpath)
os.environ["TMUX_TMPDIR"] = os.path.join(RUN_HOME, "tmux")
if _REAL_TMUX:
    os.symlink(_REAL_TMUX, os.path.join(REAL_LINKS, "tmux"))


def _end_run_home():
    shutil.rmtree(RUN_HOME, True)


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def jdump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def fread(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def running(pid):
    """the process is there and not a zombie waiting for its parent to read its exit"""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def start_time(pid):
    """when the process started, as the kernel counts it (clock ticks since boot): with its pid, a process's identity.
    None when it is gone."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def names_folder(pid, folder):
    """the process's command line or its environment names this folder (so it is that test's, or that run's)"""
    key = os.fsencode(folder)
    for what in ("cmdline", "environ"):
        try:
            with open(f"/proc/{pid}/{what}", "rb") as f:
                if key in f.read():
                    return True
        except OSError:
            pass
    return False


# G7.11 (T86): every signal the suite sends by a remembered pid. A scratch run with an audit hook saw 88 signals from
# the cleanups of 12 tests, 38 of them to pids that could not be shown to be the run's own (gone, or alive and
# another program's). A pid is only a number: between the moment it is written down and the moment it is signalled
# the process can end and the number can be given to another program.
NOT_SHOWN = []          # (test, pid, why): signals that were not sent; the test that asked fails
_SIGNAL = {"shown": None}


def parent_of(pid):
    """the id of the process's parent, None when it is gone"""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def shown_mine(pid, folder, started=None):
    """How this process is shown to be the test's still, or "" when it is not: its command line or environment names
    the test's folder; it started at the very time that was remembered with its pid; or it is this process's own
    child, not yet read (the kernel keeps a child's number for its parent until the parent has read its exit)."""
    if started is not None and started == start_time(pid):
        return "its start time"
    if parent_of(pid) == os.getpid():
        return "this process's own child"
    if folder and names_folder(pid, folder):
        return "the test's folder"
    return ""


def signal_mine(pid, sig, folder, started=None, test=""):
    """Send sig to pid only when it is shown to be this test's still (shown_mine). True: sent. False: the process is
    gone or has ended and waits to be read (nothing to signal), or it was not shown to be ours: then nothing is sent,
    it is written down, and the test that asked fails when it ends."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 1 or pid == os.getpid():
        return False
    was = start_time(pid)
    if was is None or not running(pid):
        return False                                    # gone: no signal to a number
    if not shown_mine(pid, folder, started):
        # A process that is ending gives its memory back first: for a moment it is still listed and has no command
        # line and no environment to read, so nothing can show whose it is. It is given three seconds to finish
        # ending. One that stays like that is written down like any other that was not shown.
        end = time.time() + 3
        while cmdline_of(pid) == "" and start_time(pid) == was and running(pid) and time.time() < end:
            time.sleep(0.05)
        if start_time(pid) != was or not running(pid):
            return False                                # it ended while it was being read
        NOT_SHOWN.append((test, pid, f"{cmdline_of(pid)!r}: neither this test's folder in its command line or "
                                     "environment, nor the start time remembered with it, nor a child of the suite"))
        return False
    _SIGNAL["shown"] = pid
    try:
        os.kill(pid, sig)
    except OSError:
        return False
    finally:
        _SIGNAL["shown"] = None
    return True


def _signal_hook(event, args):
    """The net under signal_mine (the first part of the guard's hook, T87): every os.kill and os.killpg of this
    process passes here. A signal to a pid that is not shown to be the running test's own is refused before it is
    sent, and fails that test. Signal 0 sends nothing (it asks whether a process is there) and passes."""
    if event[:7] != "os.kill" or _guard_out():
        return
    pid, sig = args[0], args[1]
    if event == "os.kill" and (sig == 0 or pid == os.getpid() or pid == _SIGNAL["shown"]
                               or (pid > 1 and shown_mine(pid, GUARD["tmp"]))):
        return
    test = WATCH["test"] or GUARD["test"] or "no test"
    why = ("a whole process group" if event == "os.killpg" or pid <= 1 else
           f"{cmdline_of(pid)!r}: not shown to be the test's own")
    NOT_SHOWN.append((test, pid, why))
    raise GuardRefused(f"signal: {test} asked to signal pid {pid} ({why}); not sent")


sys.addaudithook(_signal_hook)


def cmdline_of(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="replace").strip()[:200]
    except OSError:
        return "(gone while being read)"


def gone_soon(find, within=5.0):
    """What find() still lists after waiting up to `within` seconds for it to list nothing (G7.2). A process that was
    told to end exits a moment later, so a test that reads once can see it still there; one that stays is still caught."""
    end = time.time() + within
    while True:
        left = find()
        if not left or time.time() >= end:
            return left
        time.sleep(0.2)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "tardigrade.py")
MARK_TEXT = "made by Windstorm Tardigrade's test suite for one run in this scratch home"
FAKE = os.path.join(ROOT, "tests", "fake_claude.py")
FAKE_HERDR = os.path.join(ROOT, "tests", "fake_herdr.py")
TMUX = os.path.join(REAL_LINKS, "tmux") if _REAL_TMUX else None
REAL_PS = shutil.which("ps")
WAIT = float(os.environ.get("TARDIGRADE_TEST_WAIT", "40"))

# Zero survivors: a test's tmux servers and stand-ins end even when the run itself is stopped. unittest runs no
# tearDown or cleanup on SIGTERM (timeout, a stopped job) or SIGHUP (a closed terminal), so the live tests' own
# cleanups run here first. Each test registers its cleanup the moment its folder exists.
LIVE = []


def _end_live(*_):
    for t in list(LIVE):
        try:
            t.end_all()
        except AssertionError as e:                 # stopped or exiting: each test's cleanup still runs, and says so
            sys.stderr.write(f"{e}\n")
    _end_run_home()


def _on_signal(signum, _frame):
    _end_live()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


for _sig in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(_sig, _on_signal)
import atexit  # noqa: E402
atexit.register(_end_live)

# THE SUITE'S GUARD (T78, family rule 23). From 0.1.10 the test setting counts only with the suite's marker in the
# test's own scratch home (G5.15); anywhere else the tool acts for real, and the home of the account that runs the
# suite may hold a live install and running sessions. So no test but T78's own may run the tool with the setting
# ignored, and none may run it with a home outside the test's own temporary folder. A run of the program is refused
# before it starts; a load in the test's process is refused right after it (nothing has run yet but the module's
# own lines). Either way the test fails, whatever it asserts: the refusal is raised and also kept, and Base checks
# what was kept when the test ends.
GUARD = {"tmp": None, "test": None, "caught": []}
GUARD_SETTING_MAY_BE_IGNORED = {"test_G5_15_the_testing_setting_alone_changes_nothing_and_says_so"}
_RUNS_TOOL = re.compile(r"(?:^|[\s/\"'])tardigrade\.py(?=$|[\s\"'])")


class GuardRefused(AssertionError):
    pass


def suite_marked(home):
    """the suite's marker is in home, as the tool reads it (a regular file, not a link, its words first)"""
    p = os.path.join(home, "tardigrade-test-home")
    try:
        return not os.path.islink(p) and os.path.isfile(p) and fread(p).startswith(MARK_TEXT)
    except OSError:
        return False


def guard_check(home, testing, how):
    tmp, test = GUARD["tmp"], GUARD["test"]
    if tmp is None:
        why = "no test that made its own folder is running"
    elif not home or not (os.path.realpath(home) + os.sep).startswith(os.path.realpath(tmp) + os.sep):
        why = f"its home {home!r} is not inside the test's own folder {tmp}"
    elif not testing and test not in GUARD_SETTING_MAY_BE_IGNORED:
        why = "the test setting is ignored there (no suite marker in that home), so the tool would act for real"
    else:
        return
    msg = f"guard: {test or 'no test'} ran the tool ({how}) and {why}"
    GUARD["caught"].append(msg)
    raise GuardRefused(msg)


_popen_init = subprocess.Popen.__init__


def _guarded_popen_init(self, args, *a, **kw):
    argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
    line = " ".join(os.fsdecode(x) if isinstance(x, (bytes, os.PathLike)) else str(x) for x in argv)
    if _RUNS_TOOL.search(line):
        env = kw.get("env")
        env = os.environ if env is None else env
        home = env.get("HOME")
        guard_check(home, env.get("TARDIGRADE_TESTING") == "1" and bool(home) and suite_marked(home),
                    "as a program: " + line[:160])
    _popen_init(self, args, *a, **kw)


subprocess.Popen.__init__ = _guarded_popen_init
_spec_from_file_location = importlib.util.spec_from_file_location


def _guarded_spec(name, location=None, *a, **kw):
    spec = _spec_from_file_location(name, location, *a, **kw)
    if location is not None and os.path.basename(os.fspath(location)) == "tardigrade.py" and spec and spec.loader:
        run = spec.loader.exec_module

        def exec_module(module):
            run(module)
            guard_check(module.HOME, module.TESTING, "loaded in the test's process")
        spec.loader.exec_module = exec_module
    return spec


importlib.util.spec_from_file_location = _guarded_spec

# Part 3 (T87): a hook in the test process counts, while a test runs: an open, a listing or a change in the real home
# outside this checkout and Python's own libraries (a rename, link or copy at either end); a child handed the real
# home or none, or no tmux folder of its own, or a PATH that finds this machine's own copy of one of part 2's
# programs before a stand-in or a copy in a temporary folder, or one of them by its own path; and each time a loud
# stand-in was reached. Part 4: the count fails the test in its own result, at its end, whatever it asserted and
# also when it swallowed the error. THE GUARD HAS NO SWITCH: no setting takes it out. Only the gate test's planted
# cases run with it out (_GUARD_OUT), and anything else found there fails the test that meets it.
# What the hook cannot see: a read that only stats a path, and what a child does by itself (parts 1 and 2 are for
# those).
WATCH = {"case": None, "test": None, "hits": [], "mark": 0}
_GUARD_OUT = []
GATE_TEST = "test_G5_18_the_guard_catches_each_kind_in_a_planted_test_and_only_the_gate_can_take_it_out"
_TEMP_ROOTS = sorted({TMP_ROOT, os.path.realpath(tempfile.gettempdir()), "/tmp"})


def _python_own():
    import site
    own = [ROOT, sys.prefix, sys.base_prefix, sys.exec_prefix]
    try:
        own.append(site.getusersitepackages())
    except Exception:
        pass
    return sorted({os.path.realpath(x) for x in own if x})


_EXEMPT = _python_own()
_PATH_EVENTS = {"open": (0,), "os.listdir": (0,), "os.scandir": (0,), "os.walk": (0,), "glob.glob": (0,),
                "os.chmod": (0,), "os.chown": (0,), "os.remove": (0,), "os.rmdir": (0,), "os.mkdir": (0,),
                "os.utime": (0,), "os.truncate": (0,), "os.rename": (0, 1), "os.link": (0, 1), "os.symlink": (0, 1),
                "shutil.rmtree": (0,), "shutil.copyfile": (0, 1), "shutil.copymode": (0, 1), "shutil.copystat": (0, 1),
                "shutil.copytree": (0, 1), "shutil.move": (0, 1), "shutil.chown": (0,), "tempfile.mkstemp": (0,),
                "tempfile.mkdtemp": (0,)}


def _under(path, roots):
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _in_real_home(p):
    if REAL_HOME == os.sep or not isinstance(p, (str, bytes, os.PathLike)):
        return False
    try:
        q = os.path.realpath(os.fsdecode(p))
    except (TypeError, ValueError, OSError):
        return False
    return _under(q, [REAL_HOME]) and not _under(q, _EXEMPT)


def _child_hits(argv, env):
    env = os.environ if env is None else env
    if isinstance(argv, (str, bytes, os.PathLike)):
        argv = [argv]
    argv = [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in (argv or [])]
    line, hits = " ".join(argv)[:120], []
    home, tdir, path = env.get("HOME"), env.get("TMUX_TMPDIR"), env.get("PATH")
    if not home:
        hits.append(f"a child with no HOME: {line}")
    elif os.path.realpath(home) == REAL_HOME:
        hits.append(f"a child handed the real home: {line}")
    if not tdir or os.path.join(os.path.realpath(tdir), f"tmux-{os.getuid()}") in REAL_TMUX_DIRS:
        hits.append(f"a child with no tmux folder of its own (TMUX_TMPDIR {tdir or 'not set'}): {line}")
    for name in PROGRAMS:
        found = shutil.which(name, path=os.defpath if path is None else path)
        if found and not _under(os.path.abspath(found), _TEMP_ROOTS):
            hits.append(f"a child whose PATH finds this machine's own {name} ({found}) before a stand-in: {line}")
            break
    if argv and os.sep in argv[0] and os.path.basename(argv[0]) in PROGRAMS \
            and not _under(os.path.abspath(argv[0]), _TEMP_ROOTS):
        hits.append(f"this machine's own {os.path.basename(argv[0])}, by its path: {line}")
    return hits


def _guard_out():
    case = WATCH["case"]
    return case is not None and any(c is case for c in _GUARD_OUT)


def _watch_hook(event, args):
    if WATCH["case"] is None or (event not in _PATH_EVENTS and event[:10] != "subprocess" and event[:3] != "os."):
        return
    if _guard_out():
        return
    try:
        if event in _PATH_EVENTS:
            for i in _PATH_EVENTS[event]:
                if i < len(args) and _in_real_home(args[i]):
                    WATCH["hits"].append(f"{event} in the home of the account that runs the suite: "
                                         f"{os.fsdecode(args[i])}")
        elif event == "subprocess.Popen":
            WATCH["hits"] += _child_hits(args[1], args[3])
        elif event in ("os.exec", "os.posix_spawn"):
            WATCH["hits"] += _child_hits(args[1], args[2])
    except Exception as e:                      # the hook never breaks the call it watches; it fails the test
        WATCH["hits"].append(f"the guard's hook itself failed at {event}: {e!r}")


sys.addaudithook(_watch_hook)


def _unplanned_size():
    try:
        return os.path.getsize(UNPLANNED)
    except OSError:
        return 0


def _watch_end(case):
    """part 4: what was counted while this test ran fails it, in its own result"""
    out = any(c is case for c in _GUARD_OUT)
    name = case._testMethodName
    hits, WATCH["hits"] = list(WATCH["hits"]), []
    try:
        with open(UNPLANNED) as f:
            f.seek(WATCH["mark"])
            reached = f.read().splitlines()
    except OSError:
        reached = []
    WATCH["mark"] = _unplanned_size()
    hits += [f"a stand-in that fails loudly was reached, and the test brought none of its own: {x}" for x in reached]
    # G7.11 (T86): a signal this test asked for by a pid it could not show as its own was not sent, and fails it
    mine = [x for x in NOT_SHOWN if x[0] == name]
    NOT_SHOWN[:] = [x for x in NOT_SHOWN if x[0] != name]
    hits += [f"signal: {test} asked to signal pid {pid}, not shown to be its own ({why}); not sent"
             for test, pid, why in mine]
    if out:
        return                                  # a planted case of the gate test, run with the guard taken out
    if _GUARD_OUT and name != GATE_TEST:
        _GUARD_OUT[:] = []
        hits.append("the guard was taken out outside the gate test's planted run; it is back in")
    if hits:
        hits = list(dict.fromkeys(hits))
        raise GuardRefused(f"guard: {name}: " + "; ".join(hits[:6])
                           + (f"; and {len(hits) - 6} more" if len(hits) > 6 else ""))


_case_run = unittest.TestCase.run


def _watched_run(self, result=None):
    before = dict(WATCH)
    WATCH.update(case=self, test=self._testMethodName, hits=[], mark=_unplanned_size())
    self.addCleanup(_watch_end, self)           # registered first, so it runs last, after the test's own cleanups
    try:
        return _case_run(self, result)
    finally:
        WATCH.update(before, mark=_unplanned_size())


unittest.TestCase.run = _watched_run


def planted(act, out=False):
    """Run act() as a test of its own that swallows every error, and give its result. out=True: with the guard taken
    out for that one case (the gate test's use, G5.18 part 5)."""
    class Planted(unittest.TestCase):
        def test_planted(self):
            try:
                act()
            except Exception:
                pass
    case, res = Planted("test_planted"), unittest.TestResult()
    if out:
        _GUARD_OUT.append(case)
    try:
        case.run(res)
    finally:
        _GUARD_OUT[:] = [c for c in _GUARD_OUT if c is not case]
    return res


@unittest.skipUnless(sys.platform.startswith("linux") and TMUX, "needs Linux and tmux")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tg", dir=os.environ.get("TARDIGRADE_TEST_TMP", "/tmp"))
        GUARD.update(tmp=self.tmp, test=self._testMethodName, caught=[])
        self.addCleanup(self.guard_end)
        self.pids, self.loose = [], []
        LIVE.append(self)
        self.addCleanup(self.end_all)     # runs even when setUp fails after this line, or tearDown raises
        self.home = os.path.join(self.tmp, "h")
        self.bin = os.path.join(self.tmp, "bin")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.home, self.bin, self.work):
            os.makedirs(d)
        # G5.15 (T78): the second key of test mode, written in the scratch home this test made (TARDIGRADE_TESTING alone
        # changes nothing)
        with open(os.path.join(self.home, "tardigrade-test-home"), "w") as f:
            f.write(MARK_TEXT + "\n")
        # The stand-in is named "claude" from its first instant, as the native program (and npm from 2.1.113) is: its
        # first line names its interpreter directly, and a process started from a script keeps the script's name.
        # Through its own /usr/bin/env line it is "python3" until Python has loaded: the window of Claude Code from npm
        # at 2.1.112 or older (T56), kept in tests/fake_claude.py as the fixture of T56's test, built with its cure.
        # G4.4 failed about once in a hundred runs on that window (traced 2026-11-23).
        with open(FAKE) as f, open(os.path.join(self.bin, "claude"), "w") as g:
            g.write(f"#!{sys.executable}\n" + f.read().split("\n", 1)[1])
        os.chmod(os.path.join(self.bin, "claude"), 0o755)
        # G3.33 (T93): census and setup now ask systemctl about the boot service; a test that plants no answer gets
        # this stand-in's "no answer", never this machine's own systemctl --user (family rule 17)
        with open(os.path.join(self.bin, "systemctl"), "w") as f:
            f.write('#!/bin/sh\necho "$@" >> "$HOME/systemctl.log"\nexit 1\n')
        os.chmod(os.path.join(self.bin, "systemctl"), 0o755)
        # G5.27 (T107): uninstall asks the process list for a systemd user manager when systemctl gives no answer. A
        # test gets this stand-in's "none" (pgrep's exit 1), never the manager of whoever runs the suite
        self.manager(1)
        self.path = f"{self.bin}:{STUBS}:/usr/local/bin:/usr/bin:/bin"       # the loud stand-ins before the system's
        self.env = {"HOME": self.home, "TMUX_TMPDIR": self.tmp, "PATH": self.path, "SHELL": "/bin/bash",
                    "USER": os.environ.get("USER", "u"), "LANG": "C.UTF-8", "LC_ALL": "C", "TERM": "xterm-256color",
                    "TARDIGRADE_TESTING": "1", "TARDIGRADE_FAKE_UPTIME_S": "100000", "TARDIGRADE_FAKE_BOOT_ID": "boot-a",
                    "TARDIGRADE_FAKE_X11_DIR": os.path.join(self.tmp, "x11")}
        os.makedirs(os.path.join(self.tmp, "x11"))
        with open(os.path.join(self.tmp, "x11", "X10"), "w") as f:      # this "machine" has a desktop on display :10
            f.write("")
        # G5.14 (T75), family rule 25: a suite run under the family's clock shift hands it on to every program it starts;
        # the tool's clean environment does not carry it, so the stand-in takes it from HOME/clockshift
        if os.environ.get("LD_PRELOAD") and os.environ.get("SHIFT_SECONDS"):
            self.env.update(LD_PRELOAD=os.environ["LD_PRELOAD"], SHIFT_SECONDS=os.environ["SHIFT_SECONDS"])
            with open(os.path.join(self.home, "clockshift"), "w") as f:
                f.write(os.environ["LD_PRELOAD"] + "\n" + os.environ["SHIFT_SECONDS"] + "\n")
        self.cfg({"path": self.path, "shell": "bash", "stagger_s": 0, "settle_s": 2, "retry_min": 1, "retry_gap_s": 1,
                  "herdr_tabs": False, "tmux": TMUX, "watch_min": 0.1})
        self.sock = os.path.join(self.tmp, f"tmux-{os.getuid()}", "default")

    def load_tool(self, script=SCRIPT):
        """the tool loaded in this test's process with this test's home (the suite's marker in it) and the test
        setting, kept for the whole test: some of its functions read the environment at each call (the guard)"""
        from unittest import mock
        patch = mock.patch.dict(os.environ, HOME=self.home, TARDIGRADE_TESTING="1",
                                TARDIGRADE_FAKE_X11_DIR=os.path.join(self.tmp, "x11"))
        patch.start()
        self.addCleanup(patch.stop)
        spec = importlib.util.spec_from_file_location("tardigrade", script)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
        return tg

    def guard_end(self):
        caught, GUARD["caught"] = GUARD["caught"], []
        GUARD.update(tmp=None, test=None)
        if caught:
            raise GuardRefused("; ".join(caught))

    def end_all(self):
        """end only what this test started, each by its exact process id; every step runs even if one fails"""
        if self not in LIVE:
            return
        LIVE.remove(self)
        steps = [lambda: [self.kill(r.get("pid")) for r in self.launch_rows_as_far_as_written()],
                 lambda: [self.kill(*p) if isinstance(p, tuple) else self.kill(p) for p in self.pids],
                 self.end_loose]
        for step in steps:
            try:
                step()
            except Exception:
                pass
        for s in self.sockets():
            try:
                rc, out = self.tmux(s, "display", "-p", "#{pid}")
                if rc == 0 and out.strip().isdigit():
                    self.kill(int(out.strip()))
            except Exception:
                pass
        # anything else of this test's still running: a tardigrade run in flight when the run was stopped carries
        # the test's folder only in its HOME, not on its command line. Each is ended by its exact process id.
        left = self.end_mine(within=self.cleanup_bound)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if left:        # the bound was reached: the test fails and names what stayed; nothing wider than exact pids
            raise AssertionError("the cleanup's bound was reached and these processes of the test are still there:\n"
                                 + "\n".join(f"pid {p}: {cmdline_of(p)}" for p in left))

    cleanup_bound = 10.0

    def end_mine(self, within=10.0):
        """end every process of this test by its exact id, then scan again once those have exited, until a scan finds
        none (T84, G7.10): what a run in flight starts as it is stopped (the tmux server of a session it brings back)
        is there only after the first scan. One that outlives its SIGTERM gets SIGKILL in the next round. Returns what
        is still there when the time is up."""
        end, told = time.time() + within, set()
        while True:
            mine = self.processes_of_this_test()
            if not mine or time.time() >= end:
                return mine
            for pid in mine:        # each was just shown to name this test's folder; shown again at the signal
                signal_mine(pid, signal.SIGKILL if pid in told else signal.SIGTERM, self.tmp, test=self._testMethodName)
            told.update(mine)
            wait = min(end, time.time() + 3)
            while time.time() < wait and any(running(p) for p in mine):
                time.sleep(0.1)

    def processes_of_this_test(self):
        """this user's processes whose command line or environment names this test's folder (never this one)"""
        found, me, key = [], os.getpid(), self.tmp.encode()
        for p in os.listdir("/proc"):
            if not p.isdigit() or int(p) == me:
                continue
            try:
                if os.stat(f"/proc/{p}").st_uid != os.getuid():
                    continue
                with open(f"/proc/{p}/cmdline", "rb") as f:
                    hit = key in f.read()
                if not hit:
                    with open(f"/proc/{p}/environ", "rb") as f:
                        hit = key in f.read()
            except OSError:
                continue
            if hit:
                found.append(int(p))
        return found

    def end_loose(self):
        """end the sessions this test started outside tmux, each by its exact process id"""
        for p in self.loose:
            self.kill(p.pid)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self.loose = []

    # ----- helpers -----
    def kill(self, pid, sig=signal.SIGTERM, started=None):
        """end a process this test started, by its exact id, after showing it is still this test's (G7.11)"""
        if not isinstance(sig, signal.Signals):                                 # (pid, started) from self.pids
            sig, started = signal.SIGTERM, sig
        return signal_mine(pid, sig, self.tmp, started=started, test=self._testMethodName)

    @staticmethod
    def remembered(pid):
        """a pid with its start time, for self.pids: a process that names the test's folder nowhere"""
        return (pid, start_time(pid))

    def cfg(self, d):
        p = os.path.join(self.home, ".config", "windstorm-tardigrade")
        os.makedirs(p, exist_ok=True)
        cur = {}
        if os.path.exists(os.path.join(p, "config.json")):
            cur = jload(os.path.join(p, "config.json"))
        cur.update(d)
        jdump(cur, os.path.join(p, "config.json"))

    def sockets(self):
        d = os.path.join(self.tmp, f"tmux-{os.getuid()}")
        return [os.path.join(d, n) for n in os.listdir(d)] if os.path.isdir(d) else []

    def tmux(self, sock, *a, env=None):
        # G5.28 (T109): the test's own reads carry -u; the world below (LC_ALL=C, no TMUX) is the one in which
        # tmux prints "_" for a tab and for a non-ASCII letter to a client whose call does not
        r = subprocess.run([TMUX, "-u", "-S", sock, *a], capture_output=True, text=True, env=env or self.env, timeout=30)
        return r.returncode, r.stdout

    def tg(self, *a, **over):
        env = dict(self.env, **{k: str(v) for k, v in over.items()})
        r = subprocess.run([sys.executable, "-I", SCRIPT, *a], capture_output=True, text=True, env=env, timeout=240)
        return r.returncode, r.stdout + r.stderr

    def launches(self):
        try:
            return [json.loads(x) for x in fread(os.path.join(self.home, "launches.jsonl")).splitlines() if x.strip()]
        except OSError:
            return []

    def launch_rows_as_far_as_written(self):
        """for the cleanup: every whole row, so a stand-in caught mid-write cannot hide the ones before it"""
        rows = []
        try:
            text = fread(os.path.join(self.home, "launches.jsonl"))
        except OSError:
            return rows
        for x in text.splitlines():
            try:
                r = json.loads(x)
            except ValueError:
                continue
            if isinstance(r, dict):
                rows.append(r)
        return rows

    def wait_launches(self, n):
        end = time.time() + WAIT
        while time.time() < end:
            if len(self.launches()) >= n:
                return self.launches()
            time.sleep(0.3)
        self.fail(f"expected {n} start(s) of the stand-in claude, saw {len(self.launches())}")

    def start_lane(self, session, name, flags="--model claude-opus-5-5 --effort high --dangerously-skip-permissions",
                   extra_env=None):
        sid = str(uuid.uuid4())
        before = len(self.launches())
        pre = "".join(f"{k}={v} " for k, v in (extra_env or {}).items())
        os.makedirs(os.path.dirname(self.sock), mode=0o700, exist_ok=True)
        # the tmux server keeps its first environment, so per-session variables go on the command line
        rc, _ = self.tmux(self.sock, "new-session", "-d", "-s", session, "-c", self.work,
                          f"{pre}FAKE_SID={sid} FAKE_NAME='{name}' claude {flags}")
        self.assertEqual(rc, 0)
        self.wait_launches(before + 1)
        return sid

    def reboot(self):
        """What a reboot does to this user: every session and the tmux server end. Each by exact pid."""
        for row in self.launches():
            self.kill(row["pid"])
        for s in self.sockets():
            rc, out = self.tmux(s, "display", "-p", "#{pid}")
            if rc == 0 and out.strip().isdigit():
                self.kill(int(out.strip()))
        end = time.time() + WAIT
        while time.time() < end and any(self.tmux(s, "list-sessions")[0] == 0 for s in self.sockets()):
            time.sleep(0.3)

    def table(self):
        return jload(os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json"))

    def typed(self):
        """what was typed into the stand-in sessions: a list of {sid, text}"""
        try:
            return [json.loads(x) for x in fread(os.path.join(self.home, "typed.jsonl")).splitlines() if x.strip()]
        except OSError:
            return []

    def wait_typed(self, n):
        end = time.time() + 10
        while time.time() < end and len(self.typed()) < n:
            time.sleep(0.3)
        return self.typed()

    def script(self, name, text):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write(text)
        os.chmod(p, 0o755)
        return p

    def manager(self, rc, out=""):
        """plant pgrep's answer to "does a systemd user manager of this account run?": exit 0 with a process number,
        1 for none, as the real pgrep was seen to answer; every other question goes to the real pgrep"""
        self.script("pgrep", "#!/bin/sh\n" + f'case "$*" in "-u {os.getuid()} -x systemd") '
                    + (f'echo "{out}"; ' if out else "") + f'exit {rc};; esac\nexec /usr/bin/pgrep "$@"\n')

    def systemd_off(self):
        """plant a systemctl for which no unit of ours is there or runs: uninstall then ends as off"""
        self.script("systemctl", '#!/bin/sh\ncase "$*" in *is-active*) echo inactive; exit 3;; '
                    '*is-enabled*) echo not-found; exit 4;; '
                    '*show*) echo LoadState=not-found; echo ActiveState=inactive; echo SubState=dead; '
                    'echo KillMode=control-group; exit 0;; esac\nexit 0\n')

    def boot_answers(self, enabled, active, copy):
        """plant systemctl's answers (None: it answers nothing and exits 1) and the installed copy, or no copy"""
        sc = ['#!/bin/sh', 'echo "$@" >> "$HOME/systemctl.log"', 'case "$*" in']
        if enabled is None and active is None:
            sc.append('  *show*) exit 1;;')
        else:       # the timer as planted; the two services not running
            sc.append('  *show*record.timer*) echo LoadState=loaded; echo ActiveState=%s; echo SubState=%s; exit 0;;'
                      % (("active", "waiting") if active == "active" else ("inactive", "dead")))
            sc.append('  *show*) echo LoadState=loaded; echo ActiveState=inactive; echo SubState=dead; '
                      'echo KillMode=process; exit 0;;')
        for verb, word in (("is-enabled", enabled), ("is-active", active)):
            if word is None:
                sc.append(f'  *{verb}*) exit 1;;')
            else:
                sc.append(f'  *{verb}*) echo {word}; [ {word} = {"enabled" if verb == "is-enabled" else "active"} ] '
                          f'&& exit 0; exit 1;;')
        sc += ['esac', 'exit 0', '']
        self.script("systemctl", "\n".join(sc))
        self.script("loginctl", '#!/bin/sh\necho "Linger=yes"\nexit 0\n')
        share = os.path.join(self.home, ".local", "share", "windstorm-tardigrade")
        target = os.path.join(share, "tardigrade.py")
        if copy:
            os.makedirs(share, exist_ok=True)
            shutil.copyfile(SCRIPT, target)
        elif os.path.exists(target):
            os.remove(target)

    def start_loose(self, name):
        """a session that runs outside tmux, as in a plain terminal window"""
        sid = str(uuid.uuid4())
        before = len(self.launches())
        import pty
        master, slave = pty.openpty()          # a terminal window gives the session a terminal
        term = os.path.join(self.tmp, "term")  # and is its parent: a program named as a terminal is
        if not os.path.exists(os.path.join(term, "xterm")):
            os.makedirs(term, exist_ok=True)
            with open(os.path.join(term, "xterm"), "w") as f:
                f.write('#!/bin/bash\n"$@"\n')
            os.chmod(os.path.join(term, "xterm"), 0o755)
        p = subprocess.Popen([os.path.join(term, "xterm"), os.path.join(self.bin, "claude")], cwd=self.work, stdin=slave,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                             env=dict(self.env, FAKE_SID=sid, FAKE_NAME=name))
        os.close(slave)
        self.addCleanup(os.close, master)
        self.loose.append(p)
        self.wait_launches(before + 1)
        time.sleep(0.5)
        return sid

    def state(self, name):
        return os.path.join(self.home, ".local", "state", "windstorm-tardigrade", name)

    # ----- failed reads (T52, T53, T55): a read that failed is "cannot tell", never "nothing runs" -----
    def ps_breaks(self):
        """a ps in front of the real one: it fails (exit 1) while HOME/psfail exists, and notes each failure"""
        p = os.path.join(self.bin, "ps")
        with open(p, "w") as f:
            f.write(f'#!/bin/sh\nif [ -e "$HOME/psfail" ]; then echo x >> "$HOME/ps-failed"; exit 1; fi\nexec {REAL_PS} "$@"\n')
        os.chmod(p, 0o755)

    def ps_fail(self, on=True):
        f = os.path.join(self.home, "psfail")
        if on:
            open(f, "w").close()
        elif os.path.exists(f):
            os.remove(f)

    def tmux_breaks(self, how, only=()):
        """a tmux in front of the real one whose list-panes fails (exit 1) or does not answer (sleeps past the
        6 s the script waits) while HOME/tmuxfail exists: on every socket, or only on the sockets listed in it;
        everything else goes to the real tmux"""
        p = os.path.join(self.bin, "tmux-breaks")
        act = "exit 1" if how == "fails" else "exec sleep 9"
        with open(p, "w") as f:
            f.write('#!/bin/sh\nif [ -e "$HOME/tmuxfail" ]; then\n'
                    '  s=""; p=""; for a; do [ "$p" = -S ] && s="$a"; p="$a"; done\n'
                    '  if [ ! -s "$HOME/tmuxfail" ] || grep -qxF -- "$s" "$HOME/tmuxfail"; then\n'
                    f'    for a; do [ "$a" = list-panes ] && {{ {act}; }}; done\n  fi\nfi\nexec {TMUX} "$@"\n')
        os.chmod(p, 0o755)
        self.cfg({"tmux": p})
        with open(os.path.join(self.home, "tmuxfail"), "w") as f:
            f.write("".join(s + "\n" for s in only))

    def logged(self, text):
        try:
            return fread(self.state("tardigrade.log")).count(text)
        except OSError:
            return 0

    def until(self, cond, what):
        end = time.time() + WAIT
        while time.time() < end:
            if cond():
                return
            time.sleep(0.1)
        self.fail(f"waited {WAIT:g} s for: {what}")

    def by_hand(self, sid, session):
        """a person starts this session by hand, in a tmux session of their own; returns when it runs"""
        n = len(self.launches())
        self.tmux(self.sock, "new-session", "-d", "-s", session, "-c", self.work, f"MY_SETTING=byhand claude --resume {sid}")
        self.wait_launches(n + 1)

    def revive_bg(self, boot_id, **over):
        return subprocess.Popen([sys.executable, "-I", SCRIPT, "revive", "--boot"],
                                env=dict(self.env, TARDIGRADE_FAKE_UPTIME_S="100", TARDIGRADE_FAKE_BOOT_ID=boot_id, **over),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


class Record(Base):
    def test_G1_1_record_writes_down_each_live_session(self):
        sid = self.start_lane("alpha", "Alpha Lane")
        rc, out = self.tg("record", "--force")
        self.assertEqual(rc, 0, out)
        rows = self.table()["lanes"]
        self.assertEqual(len(rows), 1, out)
        r = rows[0]
        self.assertEqual((r["session_id"], r["name"], r["cwd"]), (sid, "Alpha Lane", self.work))
        self.assertEqual((r["model"], r["effort"], r["mode"]), ("claude-opus-5-5", "high", "bypass"))
        self.assertEqual((r["host"], r["tmux_socket"], r["tmux_session"]), ("tmux", self.sock, "alpha"))

    def test_G1_2_record_skips_helper_sessions(self):
        self.start_lane("alpha", "Alpha Lane")
        self.start_lane("kid", "Child", extra_env={"CLAUDE_CODE_CHILD_SESSION": "1"})
        self.tg("record", "--force")
        self.assertEqual([r["name"] for r in self.table()["lanes"]], ["Alpha Lane"])

    def test_G1_14_a_one_shot_run_is_never_a_lane_wherever_it_runs(self):
        self.start_lane("alpha", "Alpha Lane")
        # claude -p in a tmux pane, as a script or a scheduler runs it: marked "interactive" by Claude Code
        self.start_lane("printrun", "ptry-a1", flags="-p 'say ok' --model claude-haiku-5-5")
        # a claude whose input is not a terminal
        self.start_lane("noinput", "No Input", flags="--model claude-opus-5-5 < /dev/null")
        # a claude started inside another Claude session (its Bash tool, its pane): the parent is a claude
        outer = os.path.join(self.tmp, "outer")
        os.makedirs(outer)
        with open(os.path.join(outer, "claude"), "w") as f:
            f.write(f'#!/bin/bash\n"{self.bin}/claude" "$@"\n')
        os.chmod(os.path.join(outer, "claude"), 0o755)
        before, sid = len(self.launches()), str(uuid.uuid4())
        rc, _ = self.tmux(self.sock, "new-session", "-d", "-s", "inner", "-c", self.work,
                          f"FAKE_SID={sid} FAKE_NAME='Inner Run' {outer}/claude --model claude-opus-5-5")
        self.assertEqual(rc, 0)
        self.wait_launches(before + 1)
        # and the other way round: a session in a Herdr server that someone started from a Claude session is a lane
        hd = os.path.join(self.tmp, "hd")
        os.makedirs(hd)
        with open(os.path.join(hd, "herdr"), "w") as f:
            f.write('#!/bin/bash\n"$@"\n')
        holder = os.path.join(self.tmp, "holder")
        os.makedirs(holder)
        with open(os.path.join(holder, "claude"), "w") as f:
            f.write(f'#!/bin/bash\n"{hd}/herdr" "{self.bin}/claude" "$@"\n')
        for x in (os.path.join(hd, "herdr"), os.path.join(holder, "claude")):
            os.chmod(x, 0o755)
        before, sid2 = len(self.launches()), str(uuid.uuid4())
        rc, _ = self.tmux(self.sock, "new-session", "-d", "-s", "herdrhost", "-c", self.work,
                          f"FAKE_SID={sid2} FAKE_NAME='Under Herdr' {holder}/claude --model claude-opus-5-5")
        self.assertEqual(rc, 0)
        self.wait_launches(before + 1)
        rc, out = self.tg("record", "--force")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(r["name"] for r in self.table()["lanes"]), ["Alpha Lane", "Under Herdr"])
        rc, out = self.tg("revive", "--dry-run", "--all")
        self.assertNotIn("ptry-a1", out)

    def test_G1_3_record_waits_while_the_machine_is_still_starting(self):
        self.start_lane("alpha", "Alpha Lane")
        rc, out = self.tg("record", TARDIGRADE_FAKE_UPTIME_S=120)
        self.assertIn("skipped", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")))
        self.cfg({"auto_revive": True})       # armed: even a long-running machine waits for this boot's revive
        rc, out = self.tg("record")
        self.assertIn("skipped", out)

    def test_G1_4_record_never_replaces_the_record_with_an_empty_list(self):
        self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        self.reboot()
        rc, out = self.tg("record", "--force")
        self.assertIn("kept the previous record", out)
        self.assertEqual(len(self.table()["lanes"]), 1)

    def test_G1_5_a_session_that_went_down_moves_to_the_down_list(self):
        a = self.start_lane("alpha", "Alpha Lane")
        self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.kill([x for x in self.launches() if x["sid"] == a][0]["pid"])
        time.sleep(1.5)
        self.tg("record", "--force")
        t = self.table()
        self.assertEqual([r["name"] for r in t["lanes"]], ["Beta Lane"])
        self.assertEqual([r["name"] for r in t["down"]], ["Alpha Lane"])
        self.assertTrue(os.path.exists(os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json.prev")))

    def test_G1_6_a_token_is_never_written_down(self):
        self.start_lane("alpha", "Alpha Lane", extra_env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-secret-value-123"})
        self.tg("record", "--force")
        text = fread(os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json"))
        self.assertNotIn("sk-secret-value-123", text)
        row = self.table()["lanes"][0]
        self.assertEqual(row["token_keys"], ["CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertRegex(row["token_print"], r"^[0-9a-f]{8}$")      # a fingerprint, not the token
        log = fread(self.state("tardigrade.log"))
        self.assertNotIn("sk-secret-value-123", log)

    def test_G1_7_sessions_on_a_skipped_tmux_socket_are_left_to_the_other_tool(self):
        self.start_lane("alpha", "Alpha Lane")
        self.cfg({"skip_tmux_sockets": ["default"]})
        self.tg("record", "--force")
        self.assertEqual(self.table()["lanes"], [])
        rc, out = self.tg("census", "--json")
        self.assertEqual(json.loads(out)["running"], 1)     # still counted: the census shows everything

    def test_G1_8_a_session_that_is_still_starting_is_not_taken_for_down(self):
        self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        self.reboot()
        with open(os.path.join(self.home, "slow"), "w") as f:
            f.write("60")                      # Claude Code takes a minute to load on this "machine"
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        rc, out = self.tg("census", "--json")
        d = json.loads(out)
        self.assertEqual((d["running"], d["down"]), (1, 0), out)
        self.assertEqual((d["sessions"]["hidden"][0]["name"], d["sessions"]["hidden"][0]["status"]), ("Alpha Lane", "starting"))
        self.tg("record", TARDIGRADE_FAKE_UPTIME_S=100)      # the timer fires while it is still loading
        t = self.table()
        self.assertEqual(([r["name"] for r in t["lanes"]], t["down"]), (["Alpha Lane"], []))


    def test_G1_15_a_process_in_a_folder_to_leave_alone_has_only_its_folder_read(self):
        agents = os.path.join(self.tmp, "agents")
        os.makedirs(os.path.join(agents, "a1"))
        a = self.start_lane("alpha", "Alpha Lane")
        before = len(self.launches())                   # another tool's agent, with something of its own in its environment
        rc, _ = self.tmux(self.sock, "new-session", "-d", "-s", "agent1", "-c", os.path.join(agents, "a1"),
                          f"AGENT_OWN_WORDS=not-for-us FAKE_SID={uuid.uuid4()} FAKE_NAME='Agent One' claude --agent-own-flag")
        self.assertEqual(rc, 0)
        self.wait_launches(before + 1)
        agent_pid = self.launches()[-1]["pid"]
        self.tg("record", "--force")                    # recorded once, before the setting: revive must still leave it
        self.assertEqual(sorted(r["name"] for r in self.table()["lanes"]), ["Agent One", "Alpha Lane"])
        old_row = [r for r in self.table()["lanes"] if r["name"] == "Agent One"][0]
        self.cfg({"skip_cwd_prefix": [agents]})
        rc, out = self.tg("record", "--force")
        t = self.table()
        self.assertEqual([r["name"] for r in t["lanes"]], ["Alpha Lane"], out)
        self.assertEqual([r["name"] for r in t["down"]], [], "a session left alone is not down: it is not ours")
        rc, out = self.tg("census")
        self.assertIn("Left alone in a skip_cwd_prefix folder: 1 Claude process(es). Only their folder read; not brought back.", out)
        self.assertNotIn("Agent One", out)
        # nothing more of it is read by our code: not its environment, its arguments or its status file
        import importlib.util
        tg = self.load_tool()
        tg.HOME, tg.CFG["skip_cwd_prefix"] = self.home, [agents]
        read = []
        for fn in ("env_facts", "argv_of", "session_json", "has_tty"):
            real = getattr(tg, fn)
            setattr(tg, fn, (lambda real: lambda pid, *x, **k: (read.append(pid), real(pid, *x, **k))[1])(real))
        tg.live(with_launch=False)
        tg.running_sids()
        self.assertNotIn(agent_pid, read)
        self.assertEqual(tg.LEFT_ALONE[0], 1)
        # a row for it that was recorded before the setting is never started
        self.reboot()
        t = self.table()
        t["lanes"] = t["lanes"] + [old_row]
        jdump(t, os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json"))
        # seen on a real machine (strace): revive opened such a row's conversation, to look for an /exit, before
        # it saw the folder. Its conversation ends on /exit here: a revive that reads it says "ended it with /exit".
        cd = os.path.join(self.home, ".claude", "projects", "-agents-a1")
        os.makedirs(cd, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 5)) + ".000Z"
        with open(os.path.join(cd, old_row["session_id"] + ".jsonl"), "w") as f:
            f.write(json.dumps({"type": "user", "timestamp": stamp, "entrypoint": "sdk-cli", "message": {"content":
                                "<command-name>/exit</command-name>\n<command-message>exit</command-message>"}}) + "\n")
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.wait_launches(n + 1)
        time.sleep(2)
        self.assertEqual([x["sid"] for x in self.launches()[n:]], [a], out)
        self.assertIn("skip  Agent One: its folder is in skip_cwd_prefix; left alone, never started", out)
        self.assertNotIn("ended it with /exit", out)
        # nor does a record that finds nothing live read it (its conversation also says print-mode run: a record
        # that read it would move it to the down list)
        self.reboot()
        rc, out = self.tg("record")
        self.assertIn("record: 0 live sessions; kept the previous record (2 sessions)", out)
        self.assertNotIn("print-mode run", out)
        self.assertEqual(sorted(r["name"] for r in self.table()["lanes"]), ["Agent One", "Alpha Lane"])

    def test_G1_9_a_session_outside_tmux_is_recorded_for_the_socket_and_prefix_in_the_settings(self):
        self.cfg({"loose_socket": "work", "loose_prefix": "pane-", "loose_skip": ["^$", "^scratch"]})
        sid = self.start_loose("Docs Rewrite")
        self.start_loose("")                    # a plain terminal with no name
        self.start_loose("scratch pad")
        self.tg("record", "--force")
        rows = self.table()["lanes"]
        self.assertEqual([r["name"] for r in rows], ["Docs Rewrite"])
        work = os.path.join(self.tmp, f"tmux-{os.getuid()}", "work")
        self.assertEqual((rows[0]["tmux_socket"], rows[0]["tmux_session"]), (work, "pane-docs-rewrite"))
        self.end_loose()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"][-1], sid)
        self.assertEqual(self.tmux(work, "has-session", "-t", "=pane-docs-rewrite")[0], 0)

    def test_G1_10_the_record_is_also_written_as_an_eight_column_table_when_asked(self):
        tsv = os.path.join(self.home, "lanes.tsv")
        self.cfg({"tsv_path": tsv, "tsv_socket": "default"})
        sid = self.start_lane("alpha", "Alpha Lane")
        self.start_loose("Loose One")           # comes back on another socket, so it is not in this table
        self.tg("record", "--force")
        lines = fread(tsv).splitlines()
        self.assertTrue(lines[0].startswith("#"))
        self.assertTrue(lines[0].endswith("session\tsession_id\tcwd\tmodel\teffort\tmode\ttab_label\tworkspace_label"))
        self.assertEqual(lines[1:], ["\t".join(["alpha", sid, self.work, "claude-opus-5-5", "high", "bypass", "", ""])])

    def test_G1_11_sessions_that_vanish_together_stay_on_the_record(self):
        sids = [self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta", "gamma", "delta")]
        self.tg("record", "--force")
        for row in self.launches():             # three of four end in the same moment: a crash, not a person
            if row["sid"] in sids[1:]:
                self.kill(row["pid"])
        time.sleep(1.5)
        rc, out = self.tg("record")
        self.assertIn("vanished together", out)
        t = self.table()
        self.assertEqual(len(t["lanes"]), 4)
        self.assertEqual(t["down"], [])
        self.assertEqual(sorted(r["name"] for r in t["lanes"] if r.get("lost_since")), ["Beta Lane", "Delta Lane", "Gamma Lane"])
        rc, text = self.tg("census")
        self.assertIn("vanished together", text)
        n = len(self.launches())
        self.tg("revive")
        self.assertEqual(sorted(x["sid"] for x in self.wait_launches(n + 3)[n:]), sorted(sids[1:]))
        for row in self.launches()[n:]:
            self.kill(row["pid"])
        time.sleep(1.5)
        self.tg("record", "--force")            # the person says: what runs now is the truth
        t = self.table()
        self.assertEqual(([r["name"] for r in t["lanes"]], len(t["down"])), (["Alpha Lane"], 3))

    def test_G1_13_a_session_replaced_in_its_own_tmux_session_is_not_listed_as_down(self):
        a = self.start_lane("alpha", "Alpha Lane")
        self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.kill([x for x in self.launches() if x["sid"] == a][0]["pid"])
        time.sleep(1.5)
        a2 = self.start_lane("alpha", "Alpha Lane")        # a new conversation in the same tmux session
        self.tg("record", "--force")
        t = self.table()
        self.assertEqual(sorted(r["session_id"] for r in t["lanes"] if r["name"] == "Alpha Lane"), [a2])
        self.assertEqual(t["down"], [])
        rc, out = self.tg("census", "--json")
        self.assertEqual(json.loads(out)["down"], 0)

    def test_G1_12_the_model_recorded_is_the_one_in_use_not_the_one_it_started_with(self):
        sid = self.start_lane("alpha", "Alpha Lane")       # started with --model claude-opus-5-5
        d = os.path.join(self.home, ".claude", "projects", "-work")
        os.makedirs(d)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 5)) + ".000Z"
        with open(os.path.join(d, sid + ".jsonl"), "w") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": stamp,
                                "message": {"model": "claude-sonnet-5-5", "content": []}}) + "\n")
        self.tg("record", "--force")
        self.assertEqual(self.table()["lanes"][0]["model"], "claude-sonnet-5-5")

    def test_G1_17_a_session_whose_own_file_cannot_be_read_keeps_its_row_and_the_rest_is_written(self):
        # T54: a probe made each read fail in turn. With one of three sessions' own session file unreadable, record
        # put that running session on the down list: the next reboot would not have brought it back.
        a, b, c = (self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta", "gamma"))
        self.tg("record", "--force")
        was = {r["session_id"]: r for r in self.table()["lanes"]}
        pid = {x["sid"]: x["pid"] for x in self.launches()}
        sf = os.path.join(self.home, ".claude", "sessions", f"{pid[a]}.json")
        whole = fread(sf)
        self.kill(pid[c])                                   # Gamma really goes down: the rest of the record is written
        time.sleep(1.5)
        first = 0
        for how, why in (("half", "it is not whole JSON"), ("folder", "IsADirectoryError: Is a directory")):
            if how == "half":
                with open(sf, "w") as f:
                    f.write(whole[:len(whole) // 2])        # caught half written
            else:
                os.remove(sf)
                os.mkdir(sf)                                # it is there and cannot be opened as a file (root too)
            rc, out = self.tg("record")
            t = self.table()
            rows = {r["session_id"]: dict(r) for r in t["lanes"]}
            self.assertEqual(sorted(rows), sorted([a, b]), out)                     # not gone
            self.assertEqual([r["session_id"] for r in t["down"]], [c], out)        # the rest is written
            mark = rows[a].pop("unread_at")
            self.assertEqual(rows[a], was[a], "its row is kept as it was")
            first = first or mark
            self.assertEqual(mark, first, "the mark is the time it first could not be read")
            self.assertGreater(rows[b]["last_seen"], was[b]["last_seen"])
            said = f"record: Alpha Lane: its session file could not be read ({why}); its row is kept as it was"
            self.assertEqual(said in out, how == "half", out)                       # said once, when it begins
            rc, cen = self.tg("census")
            self.assertIn(f"Session file could not be read: Alpha Lane is running, and its own session file could not "
                          f"be read ({why}; first at {time.strftime('%H:%M', time.localtime(first))}). Its row on "
                          "the record is kept as it was; it is not taken as gone.", cen)
            d = json.loads(self.tg("census", "--json")[1])
            self.assertEqual((d["running"], d["down"], [u["session_id"] for u in d["unread"]]), (2, 1, [a]), cen)
            self.assertNotIn("Alpha Lane", [r["name"] for r in d["sessions"]["down"]])
            n = len(self.launches())                        # never a second copy of it
            rc, out = self.tg("revive")
            self.assertIn("skip  Alpha Lane: already running on this machine", out)
            time.sleep(2)
            self.assertEqual(self.launches()[n:], [], out)
            time.sleep(1)
        # the file reads again: the row is written new, and the mark is gone
        os.rmdir(sf)
        with open(sf, "w") as f:
            f.write(whole)
        self.tg("record")
        row = [r for r in self.table()["lanes"] if r["session_id"] == a][0]
        self.assertNotIn("unread_at", row)
        self.assertGreater(row["last_seen"], was[a]["last_seen"])
        self.assertNotIn("Session file could not be read", self.tg("census")[1])
        # a session outside tmux, started without --resume: the record cannot match its process to a row. Its row is
        # kept, marked, and said as "cannot tell": not down, and not started beside the one that runs
        d_sid = self.start_loose("Docs Rewrite")
        self.tg("record", "--force")
        lp = [x for x in self.launches() if x["sid"] == d_sid][0]["pid"]
        lf = os.path.join(self.home, ".claude", "sessions", f"{lp}.json")
        whole = fread(lf)
        os.remove(lf)
        os.mkdir(lf)
        rc, out = self.tg("record")
        self.assertIn("the record cannot tell which session each is; kept as they were, not taken as gone: Docs Rewrite", out)
        t = self.table()
        self.assertIn("unread_at", [r for r in t["lanes"] if r["session_id"] == d_sid][0])
        self.assertNotIn(d_sid, [r["session_id"] for r in t["down"]])
        rc, cen = self.tg("census")
        self.assertIn("CANNOT TELL (1): on the record, last seen outside tmux.", cen)
        d = json.loads(self.tg("census", "--json")[1])
        self.assertEqual([r["session_id"] for r in d["cannot_tell_running"]], [d_sid])
        self.assertNotIn(d_sid, [r["session_id"] for r in d["sessions"]["down"]])
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.assertIn("skip  Docs Rewrite: cannot tell whether it is running.", out)
        time.sleep(2)
        self.assertNotIn(d_sid, [x["sid"] for x in self.launches()[n:]], out)
        # when that process has ended, nothing is in doubt any more: the row goes down the usual way
        self.end_loose()
        time.sleep(1)
        self.tg("record", "--force")
        t = self.table()
        self.assertEqual([r["session_id"] for r in t["down"] if r["session_id"] == d_sid], [d_sid])
        self.assertNotIn("CANNOT TELL", self.tg("census")[1])

    def test_G1_18_a_failed_pane_or_process_read_leaves_the_record_as_it_was(self):
        """T55, seen 2026-11-23 in a probe on 0.1.8.2 and on the 0.1.9 candidate: with tmux list-panes failing, record
        rewrote both sessions from their tmux places to "no terminal" under new names, so the next restore would put
        them elsewhere. The pane list (like the process list) is one shared read: when it fails nothing is known
        about any session's place, and record writes nothing that pass. A socket left by a tmux server that is gone
        (after a reboot) is not a failed read."""
        self.start_lane("alpha", "Alpha Lane")
        self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        table = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        before = fread(table)
        self.ps_breaks()
        for broken, why in (("ps", "the process list could not be read (ps ended with code 1)"),
                            ("tmux fails", "did not list its panes (tmux ended with code 1)"),
                            ("tmux does not answer", "did not list its panes (no answer within 6 s)")):
            if broken == "ps":
                self.ps_fail()
            else:
                self.ps_fail(False)
                self.tmux_breaks("fails" if broken == "tmux fails" else "hangs")
            time.sleep(1.1)                               # a record that wrote would change last_seen
            rc, out = self.tg("record")
            self.assertEqual(rc, 0, out)
            self.assertIn("record: skipped (", out, broken)
            self.assertIn(why, out, broken)
            self.assertIn("the record is kept as it was", out, broken)
            self.assertEqual(fread(table), before, f"{broken}: the record changed")
        # a skip that lasts an hour is named by census (from the review: the log alone is read by nobody)
        sk = jload(self.state("record-skipped.json"))
        self.assertLessEqual(sk["since"], time.time())
        self.assertIn("did not list its panes", sk["why"])
        jdump(dict(sk, since=sk["since"] - 3700), self.state("record-skipped.json"))
        self.assertIn("RECORD NOT WRITTEN since", self.tg("census")[1])
        os.remove(os.path.join(self.home, "tmuxfail"))
        # a stale socket, left by a tmux server that was killed, is "no panes there", as before: the record is written
        other = os.path.join(os.path.dirname(self.sock), "gone")
        self.tmux(other, "new-session", "-d", "-s", "x", "sleep 600")
        pid = int(self.tmux(other, "display", "-p", "#{pid}")[1].strip())
        self.assertTrue(self.kill(pid, signal.SIGKILL), "the tmux server this test started names its folder")
        self.until(lambda: not os.path.exists(f"/proc/{pid}"), "the killed tmux server to go")
        self.assertTrue(os.path.exists(other), "the socket of a killed server stays behind")
        rc, out = self.tg("record")
        self.assertNotIn("skipped", out)
        self.assertIn("record: 2 session(s)", out)
        self.assertEqual([(r["host"], r["tmux_session"]) for r in self.table()["lanes"]], [("tmux", "alpha"), ("tmux", "beta")])
        self.assertFalse(os.path.exists(self.state("record-skipped.json")), "the reads work again")
        self.assertNotIn("RECORD NOT WRITTEN", self.tg("census")[1])
        # a server that answers but cannot list its panes, on a socket no recorded session lives on (a test
        # socket, another program's), does not stop the record: one wedged server would freeze it for days
        stray = os.path.join(os.path.dirname(self.sock), "stray")
        self.tmux(stray, "new-session", "-d", "-s", "y", "sleep 600")
        self.tmux_breaks("fails", only=[stray])
        rc, out = self.tg("record")
        self.assertNotIn("skipped", out)
        self.assertIn("record: 2 session(s)", out)
        self.tmux(stray, "kill-server")


class Revive(Base):
    def recorded_then_rebooted(self, **kw):
        sid = self.start_lane("alpha", "Alpha Lane", **kw)
        self.tg("record", "--force")
        self.reboot()
        return sid

    def test_G5_14_the_family_clock_shift_reaches_what_the_tests_start_and_the_tool_has_no_offset(self):
        # T75, family rule 25: before each tag the suite runs at 0, +1, +7 and +30 days under the family's clock shift.
        # The tool gets no clock offset of its own; its clean environment hands on only its own list.
        src = fread(SCRIPT)
        for word in ("SHIFT_SECONDS", "LD_PRELOAD", "FAKE_NOW", "FAKE_TIME", "CLOCK_OFFSET"):
            self.assertNotIn(word, src)
        ce = src[src.index("def clean_env():"):src.index("def login_print(")]
        self.assertIn('for k in ("TMPDIR", "XDG_RUNTIME_DIR") + (("TARDIGRADE_TESTING",) if TESTING else ()):', ce)
        # a session the tool starts gets none of the tool's other variables, and the shift only by the stand-in
        self.recorded_then_rebooted()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TG_NOT_HANDED="1")
        self.assertEqual(rc, 0, out)
        new = self.wait_launches(n + 1)[-1]
        self.assertNotIn("TG_NOT_HANDED", new["seen"])
        self.assertEqual(new["shift"], os.environ.get("SHIFT_SECONDS") if
                         os.environ.get("LD_PRELOAD") else None)
        self.assertLess(abs(new["now"] - time.time()), 600)      # the session's clock is the test's, shifted or not
        # a record that really is days old, its time set by the test: a down row past down_keep_days (14) is
        # forgotten at the next record, one inside it is kept
        t = self.table()
        now = int(time.time())
        old = dict(t["lanes"][0], session_id=str(uuid.uuid4()), name="Old Lane", tmux_session="old")
        t["down"] = [dict(old, down_since=now - 15 * 86400),
                     dict(old, session_id=str(uuid.uuid4()), name="Recent Lane", down_since=now - 13 * 86400)]
        with open(os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json"), "w") as f:
            json.dump(t, f)
        self.tg("record")
        self.assertEqual([d["name"] for d in self.table()["down"] if d["name"] in ("Old Lane", "Recent Lane")],
                         ["Recent Lane"])

    def test_G2_1_revive_brings_a_session_back_as_itself(self):
        sid = self.recorded_then_rebooted()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertEqual(rc, 0, out)
        new = self.wait_launches(n + 1)[-1]
        a = new["argv"]
        self.assertEqual(a[a.index("--resume") + 1], sid)
        self.assertEqual(a[a.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(a[a.index("--effort") + 1], "high")
        self.assertIn("--dangerously-skip-permissions", a)
        self.assertEqual(new["cwd"], self.work)
        self.assertEqual(self.tmux(self.sock, "has-session", "-t", "=alpha")[0], 0)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual(rep["started"], ["Alpha Lane"], out)
        self.assertEqual(rep["failed"], [])

    def test_G2_49_a_start_finds_claude_by_the_path_setting_not_by_the_boot_services_path(self):
        """seen in a stock Ubuntu VM (tmux 3.4): the boot service's PATH has no ~/.local/bin, where Claude Code's own
        installer puts it. tmux gives a pane that respawn-pane starts the PATH of the tmux client that asked, so every
        start at boot, which replaces its waiting screen with respawn-pane, said "claude: command not found"."""
        sid = self.recorded_then_rebooted()
        self.cfg({"path": f"{self.bin}:/usr/bin:/bin"})
        n = len(self.launches())
        # the boot service's PATH: the system's folders and no folder with claude in it. The suite's loud stand-ins
        # come first on it (G5.18): a start that used this PATH would reach one, and never this machine's own claude
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, PATH=f"{STUBS}:/usr/local/bin:/usr/bin:/bin")
        self.assertEqual(rc, 0, out)
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"][new["argv"].index("--resume") + 1], sid)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual(rep["started"], ["Alpha Lane"], out)
        self.assertEqual(new["path"], f"{self.bin}:/usr/bin:/bin", "a path that is set is used as written")
        # with no path set: the default first (claude where its installer puts it), then what the boot service's
        # own PATH had besides, so a pane never has less than before (seen on real machines: /snap/bin, /usr/games)
        self.cfg({"path": None})
        local = os.path.join(self.home, ".local", "bin")
        os.makedirs(local, exist_ok=True)
        shutil.copy(FAKE, os.path.join(local, "claude"))
        extra = os.path.join(self.tmp, "snap-bin")
        os.makedirs(extra)
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_BOOT_ID="boot-b",
                          PATH=f"{STUBS}:/usr/local/bin::.:rel/bin:/usr/bin:/bin:{extra}:{extra}:")
        self.assertEqual(rc, 0, out)
        new = self.wait_launches(n + 1)[-1]
        # and only whole paths, once each: "", "." or "rel/bin" would mean the session's own project folder
        self.assertEqual(new["path"].split(":"), [local, "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                                                         "/usr/sbin", "/sbin", STUBS, extra], out)

    # ----- G5.19 (T88), family rule 2: a person's machine is full where a stock one is empty -----
    LIVED_IN_TMUX = ("set -g base-index 1\nsetw -g pane-base-index 1\nset -g default-shell /bin/sh\n"
                     "set -g default-command 'exec sleep 600'\nset -g remain-on-exit on\n"
                     "set -g status-right '#(date)'\nset -g @plugin 'tmux-plugins/tpm'\nrun-shell 'true'\n")
    LIVED_IN_SHELL = ('echo "welcome back"\nexport PATH="$PATH:$HOME/.local/bin"\nnvm() { :; }\n'
                      "eval \"$(echo 'export NVM_DIR=$HOME/.nvm')\"\nset -o noclobber\n")
    # the lines of tests/lived-in-list.md that the suite plants itself; the others are for the round on a stock machine
    LIVED_IN_PLANTED = ["base-index 1", "a default shell and a default command", "remain-on-exit", "destroy-unattached",
                        "a status line that runs a command", "a plugin-manager line", "prints", "asks with read",
                        "starts tmux itself", "puts ~/.local/bin late on PATH", "runs a version manager's init",
                        "sets noclobber", "ends the shell", "hands the pane to another program", "runs a program that does not end",
                        "hands the pane to another shell", "TMUX_TMPDIR",
                        "an exported token"]

    def lived_in(self, conf=None, rc=None):
        """the person's own settings, written after the session's first start so that only the revive meets them"""
        for name, text in ((".tmux.conf", conf), (".bashrc", rc)):
            path = os.path.join(self.home, name)
            if text is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(path, "w") as f:
                    f.write(text)

    def test_G5_19_what_the_tool_starts_gets_an_environment_it_built_and_a_lived_in_machine_gets_its_session_back(self):
        """T88, parts 1 and 2: the tmux server the tool starts gets an environment the tool built, by name; tmux is
        always given its socket; the units hand nothing on; and with a person's tmux settings and shell start file
        in place (the suite's share of tests/lived-in-list.md) the session comes back as itself. One of those
        settings ends every session with no window attached: the one session the tool makes is asked to stay, and
        the server's own setting is the same before and after."""
        sid = self.recorded_then_rebooted()
        work_before = sorted(os.listdir(self.work))
        self.lived_in(self.LIVED_IN_TMUX + "set -g destroy-unattached on\n", self.LIVED_IN_SHELL)
        envf = os.path.join(self.home, "desk.env")
        with open(envf, "w") as f:
            f.write("DISPLAY=:10.0\n")
        seen = self.script("tmux-seen", '#!/bin/sh\nprintf "%s %s %s\\n" "$1" "$2" "$3" >> "$HOME/tmux-calls.log"\n'
                           f'exec {shlex.quote(TMUX)} "$@"\n')
        self.cfg({"env_file": envf, "tmux": seen})
        # before: what this person's tmux settings say, asked of a server that reads them and ends again
        ask = lambda *a: self.tmux(self.sock, *a)[1].strip()
        self.assertEqual(ask("start-server", ";", "show-options", "-gv", "destroy-unattached"), "on")
        self.until(lambda: self.tmux(self.sock, "list-sessions")[0] != 0, "the asked server to have ended")
        noise = {"LIVED_IN_NOISE": "1", "CLAUDE_CODE_OAUTH_TOKEN": "tok-lived-in", "NVM_DIR": "/nowhere/.nvm",
                 "TMUX_PLUGIN_MANAGER_PATH": "/nowhere/tpm", "SSH_AUTH_SOCK": "/nowhere/agent", "EDITOR": "vi"}
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, **noise)
        self.assertEqual(rc, 0, out)
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"][new["argv"].index("--resume") + 1], sid, out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["failed"], rep.get("failed_why")), (["Alpha Lane"], [], None), out)
        # (1) the server's environment, by name: the nine, the env file's pair, and the suite's own test key
        names = {x.lstrip("-").split("=", 1)[0] for x in ask("show-environment", "-g").splitlines() if x.strip()}
        must = {"HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "PATH"}
        may = must | {"TMPDIR", "XDG_RUNTIME_DIR", "DISPLAY", "TARDIGRADE_TESTING", "PWD"}    # PWD: tmux's own line
        self.assertEqual((sorted(must - names), sorted(names - may)), ([], []), "the tmux server's environment, by name")
        self.assertEqual(new["claude_env"], [], "nothing of the caller's Claude settings reached the session")
        try:
            with open(f"/proc/{new['pid']}/environ", "rb") as f:
                got = dict(x.decode("utf-8", "replace").split("=", 1) for x in f.read().split(b"\0") if b"=" in x)
            self.assertEqual(sorted(k for k, v in noise.items() if got.get(k) == v), [],
                             "what the tool was called with stayed with the tool")
            # and the session has the person's environment on purpose, through the person's own shell start
            self.assertEqual(got.get("NVM_DIR"), os.path.join(self.home, ".nvm"))
            self.assertEqual(got["PATH"].split(":")[-1], os.path.join(self.home, ".local", "bin"))
        except OSError:
            pass                                    # no /proc: the server's own list above is the read
        # (2) the person's setting is as it was; only the one session the tool made is asked to stay
        self.assertEqual((ask("show-options", "-gv", "destroy-unattached"),
                          ask("show-options", "-v", "-t", "=alpha:", "destroy-unattached")), ("on", "off"))
        self.tmux(self.sock, "new-session", "-d", "-s", "persons-own", "sleep 30")
        self.until(lambda: self.tmux(self.sock, "has-session", "-t", "=persons-own")[0] != 0,
                   "the person's own detached session to end, as their settings say")
        self.assertEqual(self.tmux(self.sock, "has-session", "-t", "=alpha")[0], 0)
        # the pane's own process is the shell the tool started, also where the person's tmux runs commands with a
        # shell that stays above them (default-shell /bin/sh): only so can the tool read what the pane holds
        pane = ask("display", "-p", "-t", "=alpha:", "#{pane_pid} #{pane_current_command}").split()
        self.assertEqual(cmdline_of(int(pane[0])).split()[:2], ["bash", "-ic"])
        self.assertNotIn(pane[1], ("sh", "dash", "bash"), "tmux names the session's program, not a shell above it")
        # tmux always gets its socket: every call of this record, census and revive (after -u, which every call
        # of the tool's own starts with since T109)
        self.tg("record", "--force")
        rc, census = self.tg("census")
        self.assertIn("Running: 1 ", census)
        calls = fread(os.path.join(self.home, "tmux-calls.log")).splitlines()
        self.assertGreater(len(calls), 5)
        self.assertEqual([c for c in calls if not (c.startswith("-u -S " + self.tmp + os.sep) or c == "-V  ")], [])
        # (a) the mark is the tool's own, in its state folder; the session's folder and the home got nothing; the
        # census counts no session for it
        mark = self.state(os.path.join("launch", sid + ".mark"))
        self.assertEqual(fread(mark), "reached\n")
        self.assertEqual(sorted(os.listdir(self.work)), work_before)
        self.assertEqual([x for x in os.listdir(self.home) if x.endswith(".mark")], [])
        self.assertEqual(len(self.table()["lanes"]), 1)
        # the units hand nothing on, and run the installed copy
        tool = self.load_tool()
        texts = list(tool.units("/PY", "/COPY").values()) + [tool.plist("l", "/PY", "/COPY revive --boot", "exec", "")]
        for text in texts:
            self.assertNotIn("Environment", text)
        self.assertEqual(re.findall(r"^ExecStart=(.*)$", "\n".join(texts), re.M),
                         ["/PY -I /COPY revive --boot", "/PY -I /COPY record"])
        # uninstall takes the mark, and keeps a file of the person's that only has its ending
        own = self.state(os.path.join("launch", "my-own.mark"))
        with open(own, "w") as f:
            f.write("mine\n")
        self.boot_answers("disabled", "inactive", True)
        rc, out = self.tg("uninstall")
        self.assertEqual((rc, os.path.exists(mark), fread(own)), (0, False, "mine\n"), out)
        self.assertIn("  " + mark, out.split("Removed:")[1].split("Kept")[0])
        self.assertIn("  " + own, out.split("Kept, because Tardigrade did not write them")[1])
        # (3) the list for the lived-in round is beside the tests, and says which of its lines the suite plants
        lines = [x[2:].strip() for x in fread(os.path.join(ROOT, "tests", "lived-in-list.md")).splitlines()
                 if x.startswith("- ")]
        self.assertEqual(sorted(x[:-len(" (suite)")] for x in lines if x.endswith(" (suite)")),
                         sorted(self.LIVED_IN_PLANTED))
        self.assertGreaterEqual(len([x for x in lines if x.endswith(" (round)")]), 10)
        self.assertEqual([x for x in lines if not x.endswith((" (suite)", " (round)"))], [])

    ENDED_AT_ONCE = ("the shell ended before it read the session's start file. The usual cause is a line in the shell's "
                     "start files (~/.bashrc) that ends the shell, or that runs a program in the shell's place which "
                     "ends (an `exit`, an `exec tmux`). Next step: in that file, keep that line from running inside "
                     "tmux, then run `tardigrade revive`.")

    # ----- G5.19 and G1.20: every step is taken as printed, in the state the failure left, after a plain record
    # pass and with no restart between (family rule 7). Two sessions: Alpha Lane is the one a revive has to start;
    # Beta Lane was started by hand first, so a session runs and a record pass does what the timer's would.
    other = None

    def one_up_one_down(self):
        a = self.start_lane("alpha", "Alpha Lane")
        self.other = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.reboot()
        return a

    def other_up(self):
        """the person starts Beta Lane by hand (not through the shell's start files), unless it runs"""
        if self.other and self.tmux(self.sock, "has-session", "-t", "=beta")[0] != 0:
            self.by_hand(self.other, "beta")

    def names(self, key="lanes"):
        return sorted(r["name"] for r in self.table()[key])

    def pane(self, what, session="alpha"):
        return self.tmux(self.sock, "display", "-p", "-t", f"={session}:", "#{" + what + "}")[1].strip()

    def mark(self, session="alpha", mark="@tardigrade_session"):
        """the tmux option of that name on a tmux session: the id of the session Tardigrade made it for"""
        return self.tmux(self.sock, "show-options", "-qv", "-t", f"={session}:", mark)[1].strip()

    def brought_back(self):
        """A session that a revive brought back and saw loaded, in the tmux session that revive made, on a machine
        whose other session runs. Its place keeps the mark that names it. -> (its id, the stand-in's launch row)"""
        sid = self.one_up_one_down()
        self.lived_in("set -g base-index 1\n", None)
        self.other_up()
        n = len(self.launches())
        rc, out = self.tg("revive")
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"][new["argv"].index("--resume") + 1], sid, out)
        self.assertIn("revive: 1 started, 1 already running, 0 failed, 0 need you\n", out)
        self.assertIn("Running: 2 ", self.tg("census")[1])
        self.assertEqual(self.mark(), sid, "the place keeps its mark after the session has loaded")
        return sid, new

    def ends_alone(self, row):
        """its Claude Code ends by itself (no /exit), and the shell of its start file is kept in its place"""
        self.kill(row["pid"])
        self.until(lambda: self.pane("pane_current_command") == "bash", "the shell kept in its place")

    def record_pass(self, case):
        """a plain record pass, as the timer runs one every five minutes: a session that is not back stays on the
        list a revive reads (G1.20)"""
        rc, out = self.tg("record")
        self.assertEqual((rc, self.names(), self.names("down")),
                         (0, ["Alpha Lane"] + (["Beta Lane"] if self.other else []), []), f"{case}: {out}")
        return out

    def not_back(self, case, rc=None, cfg=None, conf=None):
        """One revive that the person's settings stop. It is not counted as brought back, its line names the case
        and the one next step, the report keeps it; then a plain record pass, and the census says it under the
        row, read at that moment. -> (the reason, the revive's output)"""
        self.lived_in(self.LIVED_IN_TMUX if conf is None else conf, rc)
        self.cfg(dict({"retry_min": 0.1, "claude_command": None}, **(cfg or {})))
        self.other_up()
        n = len(self.launches())
        rcode, out = self.tg("revive")
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["failed"], rep["skipped"], len(self.launches())),
                         ([], ["Alpha Lane"], ["Beta Lane"] if self.other else [], n), f"{case}: {out}")
        why = rep.get("failed_why", {}).get("Alpha Lane", "")
        gave = [x for x in out.splitlines() if "Alpha Lane: GAVE UP after" in x]
        self.assertEqual(len(gave), 1, out)
        self.assertTrue(why and gave[0].endswith(": " + why), f"{case}: the line and the report say the same: {gave} {why!r}")
        self.assertLessEqual(why.count("Next step: "), 1, f"{case}: one next step: {why}")
        self.assertIn(f"revive: 0 started, {1 if self.other else 0} already running, 1 failed, 0 need you\n", out)
        self.record_pass(case)
        census = self.tg("census")[1]
        self.assertIn(f"Running: {1 if self.other else 0} ", census, case)
        self.assertIn("      A revive did not bring it back: " + why, census, case)
        return why, out

    def back(self, sid, case, why="", sessions=("alpha",)):
        """The next step was taken as printed. With no restart in between, and after one more plain record pass,
        the same `tardigrade revive` brings the session back, once, in its own place; it says first what it is
        trying again; the place keeps the mark that names the session, and no waiting-screen mark; and the row is an
        ordinary one again."""
        self.record_pass(case)
        n = len(self.launches())
        rc, out = self.tg("revive")
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"][new["argv"].index("--resume") + 1], sid, f"{case}: {out}")
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["failed"], rep.get("failed_why"), rep.get("not_started")),
                         (["Alpha Lane"], [], None, None), f"{case}: {out}")
        self.assertIn(f"revive: 1 started, {1 if self.other else 0} already running, 0 failed, 0 need you\n", out)
        if why:
            self.assertIn("trying again Alpha Lane: last time " + why.split(" Next step:")[0], out, case)
        time.sleep(1)
        self.assertEqual(len(self.launches()), n + 1, f"{case}: started once")
        self.assertEqual(sorted(set(self.tmux(self.sock, "list-panes", "-a", "-F", "#{session_name}")[1].split())),
                         sorted(list(sessions) + (["beta"] if self.other else [])), case)
        self.assertEqual((self.mark(mark="@tardigrade_waiting"), self.mark()), ("", sid), case)
        census = self.tg("census")[1]
        self.assertIn(f"Running: {2 if self.other else 1} ", census, case)
        self.assertNotIn("A revive did not", census, case)
        self.assertNotIn("a revive was to start them", census, case)
        self.record_pass(case)
        self.assertEqual([k for r in self.table()["lanes"] for k in r if k.startswith("not_started")], [], case)
        return out

    def test_G5_19_a_shell_that_ends_before_the_start_file_is_named_so_and_is_not_counted_as_back(self):
        """T88, case 1: the session ended at once and tmux removed it (the shell start file exits, or runs tmux in
        the shell's place, which refuses to nest and ends)"""
        sid = self.one_up_one_down()
        self.script("tmux", f'#!/bin/sh\nexec {shlex.quote(TMUX)} "$@"\n')       # the pane's own tmux: the real one
        plain = "set -g base-index 1\n"
        for n, (line, cure) in enumerate((("exit 0", '[ -n "$TMUX" ] || exit 0'),
                                          ("exec tmux new-session -A -s main",
                                           '[ -n "$TMUX" ] || exec tmux new-session -A -s main'))):
            if n:
                self.reboot()                               # another restart of the machine: a new failure
            why, out = self.not_back(line, rc=f"echo hi\n{line}\n", conf=plain)
            self.assertIn("its tmux session is gone: it ended at once and tmux removed it: " + self.ENDED_AT_ONCE, out)
            self.assertNotEqual(self.tmux(self.sock, "has-session", "-t", "=alpha")[0], 0)
            # the next step, taken: the line no longer runs inside tmux
            self.lived_in(plain, f"echo hi\n{cure}\n")
            self.back(sid, line, why)

    def test_G5_19_a_pane_kept_after_its_shell_ended_is_read_as_ended_and_the_next_revive_starts_there(self):
        """T88, case 1 where the person's tmux keeps a pane whose program ended (remain-on-exit): the session is
        still there and holds nothing. Read as a shell, it was said to hold one, with no cause; and the revive
        after it said "a tmux session named alpha already exists", started nothing and counted it as running."""
        sid = self.one_up_one_down()
        why, out = self.not_back("exit, pane kept", rc="echo hi\nexit 0\n")
        self.assertIn("its tmux session holds a pane whose program has ended (kept by remain-on-exit in your tmux "
                      "settings): it ended at once: " + self.ENDED_AT_ONCE, out)
        # at the give-up nothing was touched: the ended pane is there, and it carries the id it was made for
        place = self.pane("session_id")
        self.assertEqual((self.pane("pane_dead"), self.tmux(self.sock, "show-options", "-qv", "-t", "=alpha:",
                                                             "@tardigrade_session")[1].strip()), ("1", sid))
        self.lived_in(self.LIVED_IN_TMUX, 'echo hi\n[ -n "$TMUX" ] || exit 0\n')
        self.back(sid, "exit, pane kept", why)
        self.assertEqual((self.pane("session_id"), self.pane("pane_dead")), (place, "0"), "started in that place")

    def test_G5_19_the_tool_knows_its_own_waiting_screen_under_a_default_shell_that_stays_above_the_command(self):
        """T88, seen with `default-shell /bin/sh` where sh is dash: dash runs a command as its child and stays, so
        the pane's own process was `sh -c ...`, never the waiting screen. A run stopped at its waiting screen then
        left a screen the next run did not know as its own, and that session was not brought back."""
        sid = self.recorded_then_rebooted()
        # a shell that does with `-c COMMAND` what dash does, whatever this machine's sh is: the command runs as its
        # child and the shell stays, unless the command itself says exec
        stay = self.script("stay-shell", '#!/bin/sh\nif [ "$1" = "-c" ]; then eval "$2"; exit $?; fi\n'
                           'exec /bin/sh "$@"\n')
        self.lived_in(f"set -g default-shell {stay}\n", None)
        n = len(self.launches())
        rc, out = self.tg("revive", TARDIGRADE_FAKE_STOP_AT="screen_made:1")
        self.assertEqual(rc, 137, f"the planted stop was not reached: {out}")
        self.assertEqual(cmdline_of(int(self.pane("pane_pid"))).split()[-2:], ["wait", sid],
                         "the pane's own process is the waiting screen")
        self.assertIn("WAITING, STOPPED: a waiting screen holds its place", self.tg("census")[1])
        self.assertEqual(len(self.launches()), n)
        self.back(sid, "a waiting screen under a shell that stays")
        self.assertEqual(cmdline_of(int(self.pane("pane_pid"))).split()[:2], ["bash", "-ic"], "and so is the start shell")

    def another_program(self, conf, rc):
        """T88, case 2: the shell's start files put another program into the pane. The give-up line names it; a
        revive that meets it before the step does not touch it, names it and counts the session as not started,
        never as running. -> (the session id, the reason, the program's pid)"""
        sid = self.one_up_one_down()
        why, out = self.not_back("another program", rc=rc, conf=conf)
        look = f"`{TMUX} -S {self.sock} attach -t '=alpha'`"
        self.assertIn("its tmux session holds another program (sleep): the shell's start files (~/.bashrc) started "
                      "another program in the pane (`sleep`) and it has not ended, so the session's start file was not "
                      "read. Tardigrade does not end that program. Next step: in that file, keep the line that starts "
                      "`sleep` from running inside tmux, end that program in its window "
                      f"({look}), then run `tardigrade revive`.", out)
        self.assertFalse(os.path.exists(self.state(os.path.join("launch", sid + ".mark"))))
        kids = subprocess.run(["pgrep", "-x", "sleep", "-P", self.pane("pane_pid")], capture_output=True, text=True)
        pid = int((kids.stdout.split() or [self.pane("pane_pid")])[0])
        self.assertEqual(cmdline_of(pid).split()[:1], ["sleep"])
        n, own, began = len(self.launches()), self.pane("pane_pid"), start_time(pid)
        rc2, again = self.tg("revive")
        self.assertIn("skip  Alpha Lane: not started: its tmux session alpha holds a running program (`sleep`), and "
                      f"Tardigrade does not end it. Next step: end that program in its window ({look}), then run "
                      "`tardigrade revive`.", again)
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you, 1 not started: a tmux session of "
                      "that name is in the way\n", again)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["skipped"], rep.get("in_way")), ([], ["Beta Lane"], ["Alpha Lane"]))
        self.assertEqual((len(self.launches()), self.pane("pane_pid"), start_time(pid)),
                         (n, own, began), "the program the person's file started is not touched")
        self.record_pass("another program, met again")
        census = self.tg("census")[1]
        self.assertIn("0 started, 0 failed, 0 need you, 1 not started: a tmux session of that name is in the way", census)
        self.assertIn("      A revive did not bring it back: " + why, census)
        return sid, why, pid

    def test_G5_19_another_program_in_the_pane_is_named_and_after_it_is_ended_and_its_window_goes_revive_starts_the_session(self):
        """the printed step, where ending the program closes its window (no remain-on-exit)"""
        plain = "set -g base-index 1\n"
        sid, why, pid = self.another_program(plain, "exec sleep 300\n")
        self.lived_in(plain, '[ -n "$TMUX" ] || exec sleep 300\n')
        self.kill(pid)
        self.until(lambda: self.tmux(self.sock, "has-session", "-t", "=alpha")[0] != 0, "the window to go")
        self.back(sid, "another program, window gone")

    def test_G5_19_another_program_in_the_pane_is_named_and_after_it_is_ended_and_its_pane_stays_revive_starts_there(self):
        """the printed step, where the person's tmux keeps the pane of the ended program (remain-on-exit)"""
        sid, why, pid = self.another_program(self.LIVED_IN_TMUX, "exec sleep 300\n")
        place = self.pane("session_id")
        self.lived_in(self.LIVED_IN_TMUX, '[ -n "$TMUX" ] || exec sleep 300\n')
        self.kill(pid)
        self.until(lambda: self.pane("pane_dead") == "1", "the pane to be an ended one")
        self.back(sid, "another program, pane kept")
        self.assertEqual((self.pane("session_id"), self.pane("pane_dead")), (place, "0"), "started in that place")

    def test_G5_19_another_program_in_the_pane_is_named_and_after_it_is_ended_the_shell_goes_on_and_claude_code_starts(self):
        """the printed step, where the start file ran the program without handing the shell over: once it is ended
        the shell goes on, reads the session's start file, and Claude Code starts in that window by itself. The
        `tardigrade revive` of the step then starts nothing beside it and counts it as running, which it is."""
        plain = "set -g base-index 1\n"
        sid, why, pid = self.another_program(plain, "sleep 300\n")
        self.lived_in(plain, '[ -n "$TMUX" ] || sleep 300\n')
        n = len(self.launches())
        self.kill(pid)
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"][new["argv"].index("--resume") + 1], sid)
        self.record_pass("another program, shell went on")
        rc, out = self.tg("revive")
        self.assertIn("skip  Alpha Lane: already running on this machine", out)
        self.assertIn("revive: 0 started, 2 already running, 0 failed, 0 need you\n", out)
        self.assertEqual(len(self.launches()), n + 1)
        census = self.tg("census")[1]
        self.assertIn("Running: 2 ", census)
        self.assertNotIn("A revive did not", census)

    def test_G5_19_a_start_file_that_hands_the_pane_to_another_shell_is_named_so_and_is_not_counted_as_back(self):
        """T88, case 2 for a common setting: ~/.bashrc ends with `exec zsh` or `exec fish`. The pane then holds a
        shell, but not the one that was to read the session's start file. It was read as "waits for an answer"."""
        sid = self.one_up_one_down()
        plain = "set -g base-index 1\n"
        why, out = self.not_back("another shell", rc="echo hi\nexec sh\n", conf=plain)
        self.assertIn("its tmux session holds a shell: the shell's start files (~/.bashrc) handed the pane to another "
                      "shell (`sh`), so the session's start file was not read. Next step: in that file, keep the line "
                      "that starts `sh` from running inside tmux, then run `tardigrade revive`.", out)
        place = self.pane("session_id")
        self.lived_in(plain, 'echo hi\n[ -n "$TMUX" ] || exec sh\n')
        self.back(sid, "another shell", why)
        self.assertEqual(self.pane("session_id"), place, "started in that place")

    QUESTION = ("its tmux session holds a shell: the shell's start files (~/.bashrc) had not finished when it was last "
                "looked at, so the session's start file was not read: a line there waits for an answer. Next step: "
                "`%s -S %s attach -t '=alpha'`, answer it there, and Claude Code starts in that window.")

    def test_G5_19_a_start_file_that_waits_for_an_answer_is_named_so_and_answering_it_there_starts_claude_code(self):
        """T88, case 2: the shell's start files ask something. The step is taken as printed: the question is
        answered in that window, and Claude Code starts there, with no revive and no restart."""
        sid = self.one_up_one_down()
        why, out = self.not_back("a question", rc='echo welcome\nread -p "Pick a profile: " x\n')
        self.assertIn(self.QUESTION % (TMUX, self.sock), out)
        n = len(self.launches())
        self.tmux(self.sock, "send-keys", "-t", "=alpha:", "work", "Enter")
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"][new["argv"].index("--resume") + 1], sid)
        self.record_pass("a question, answered")
        census = self.tg("census")[1]
        self.assertIn("Running: 2 ", census)
        self.assertNotIn("A revive did not", census)
        self.assertEqual(len(self.launches()), n + 1)

    def test_G5_19_a_pane_that_ends_after_the_give_up_is_read_as_it_is_now_and_the_step_then_printed_is_true(self):
        """What a revive found can change before the person reads it: the shell that waited at a question ends
        later and tmux keeps its pane. The census reads the place at that moment, so the step it prints is the one
        that is true then, and that step is taken."""
        sid = self.one_up_one_down()
        why, out = self.not_back("a question", rc='echo welcome\nread -p "Pick a profile: " x\n')
        self.assertIn(self.QUESTION % (TMUX, self.sock), out)
        self.kill(int(self.pane("pane_pid")), signal.SIGKILL)
        self.until(lambda: self.pane("pane_dead") == "1", "the pane to be an ended one")
        census = self.tg("census")[1]
        self.assertIn("      A revive did not bring it back: it ended at once: " + self.ENDED_AT_ONCE, census)
        self.assertNotIn("answer it there", census)
        self.lived_in(self.LIVED_IN_TMUX, 'echo welcome\n[ -n "$TMUX" ] || read -p "Pick a profile: " x\n')
        self.back(sid, "the pane ended later", why)

    def test_G5_19_a_start_that_ended_with_a_code_is_named_with_the_code_and_is_not_counted_as_back(self):
        """T88, case 3: the session's start file was read and its command ended: not found (127), or by itself"""
        sid = self.one_up_one_down()
        why, out = self.not_back("not found", rc="export PATH=/usr/bin:/bin\n", cfg={"claude_command": "claude-nowhere"})
        self.assertIn("its tmux session holds a shell: `claude-nowhere` ended with exit code 127, not found: the PATH "
                      "that the shell's start files (~/.bashrc) leave has no such program. Next step: set "
                      "claude_command in config.json to that program's whole path, then run `tardigrade revive`.", out)
        self.assertEqual(fread(self.state(os.path.join("launch", sid + ".mark"))), "ended 127\n")
        place = self.pane("session_id")
        # the next step, taken: the whole path in the setting
        self.cfg({"claude_command": os.path.join(self.bin, "claude")})
        self.back(sid, "not found", why)
        self.assertEqual(self.pane("session_id"), place, "started in that place")
        self.until(lambda: fread(self.state(os.path.join("launch", sid + ".mark"))) == "reached\n", "a new start's mark")
        self.reboot()                                       # another restart of the machine: a new failure
        line = "sh -c 'echo no licence; exit 3'"
        why, out = self.not_back("a code", cfg={"claude_command": line})
        self.assertIn("its tmux session holds a shell: `sh` ended by itself with exit code 3; what it said is in its "
                      f"window. Next step: `{TMUX} -S {self.sock} attach -t '=alpha'` and read it, cure what it names, "
                      "then run `tardigrade revive`.", out)
        # the next step as printed: the window says what the command said; its cause is cured; then the revive
        self.assertIn("no licence", self.tmux(self.sock, "capture-pane", "-p", "-t", "=alpha:")[1])
        self.cfg({"claude_command": None})
        self.back(sid, "a code", why)

    def test_G5_19_a_start_that_ended_with_a_code_and_whose_window_then_went_is_named_and_its_printed_step_is_true(self):
        """T88, case 3 where nothing is left to look at: the command ended with a code and the shell kept in its
        place ended too (a login start file that ends the shell), so tmux removed the session"""
        sid = self.one_up_one_down()
        with open(os.path.join(self.home, ".bash_profile"), "w") as f:
            f.write("exit 0\n")
        line = "sh -c 'echo no licence; exit 3'"
        why, out = self.not_back("a code, window gone", cfg={"claude_command": line}, conf="set -g base-index 1\n")
        self.assertIn("its tmux session is gone: `sh` ended with exit code 3, and then its tmux session ended. Next "
                      "step: `tardigrade revive --dry-run` shows the line that starts it; run that line in a terminal "
                      "to read what it says, cure what it names, then run `tardigrade revive`.", out)
        # the step as printed: the dry run shows the line, and the line says what it said
        rc, dry = self.tg("revive", "--dry-run")
        shown = [x.split("tmux default:alpha", 1)[1].strip() for x in dry.splitlines() if "tmux default:alpha" in x]
        self.assertEqual([x.startswith(line) for x in shown], [True], dry)
        said = subprocess.run(["sh", "-c", line], capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual((said.returncode, said.stdout), (3, "no licence\n"))
        self.cfg({"claude_command": None})
        self.back(sid, "a code, window gone", why)

    def test_G5_19_a_start_whose_command_has_not_started_claude_code_yet_is_named_and_both_printed_ways_on_are_true(self):
        """T88: the session's start file was read and its command is still there: a login wrapper that asks, or one
        that is still at work. Nothing is touched; answered there, or once it is done, Claude Code starts in that
        window by itself."""
        sid = self.one_up_one_down()
        ask = self.script("ask-first", '#!/bin/sh\nprintf "Which account? "\nread x\nexec claude "$@"\n')
        busy = self.script("busy-first", '#!/bin/sh\nwhile [ ! -e "$HOME/go" ]; do sleep 0.2; done\nexec claude "$@"\n')
        for k, (word, take) in enumerate((
                ("ask-first", lambda: self.tmux(self.sock, "send-keys", "-t", "=alpha:", "first", "Enter")),
                ("busy-first", lambda: open(os.path.join(self.home, "go"), "w").close()))):
            if k:
                self.reboot()                               # another restart of the machine: a new failure
            why, out = self.not_back(word, cfg={"claude_command": ask if k == 0 else busy}, conf="set -g base-index 1\n")
            self.assertEqual(why, f"its start file was read and `{ask if k == 0 else busy}` has not started Claude Code "
                             f"yet. Next step: `{TMUX} -S {self.sock} attach -t '=alpha'`: if it asks something, answer "
                             "it there and Claude Code starts in that window; if it is still at work, Claude Code "
                             "starts there when it is done.")
            self.assertEqual(fread(self.state(os.path.join("launch", sid + ".mark"))), "reached\n")
            n = len(self.launches())
            take()
            new = self.wait_launches(n + 1)[-1]
            self.assertEqual(new["argv"][new["argv"].index("--resume") + 1], sid, word)
            self.record_pass(word)
            census = self.tg("census")[1]
            self.assertIn("Running: 2 ", census, word)
            self.assertNotIn("A revive did not", census, word)

    def test_G5_19_a_tmux_session_of_that_name_that_is_not_ours_is_not_touched_and_the_printed_rename_frees_the_name(self):
        """A tmux session of the row's name that Tardigrade did not make (a tmux that brings its own sessions back
        does this). It was said to exist "already" and counted as running. Now: named, counted as not started, and
        the one step is printed whole, with a name read to be free. The step is run word for word."""
        sid = self.one_up_one_down()
        self.lived_in("set -g base-index 1\n", None)
        self.other_up()
        for name in ("alpha", "alpha-kept"):                # the person's own, and the first free name is taken too
            self.assertEqual(self.tmux(self.sock, "new-session", "-d", "-s", name, "-c", self.work, "exec sleep 300")[0], 0)
        own, n = self.pane("pane_pid"), len(self.launches())
        rc, out = self.tg("revive")
        cmd = f"{TMUX} -S {self.sock} rename-session -t '=alpha' alpha-kept-2"
        said = ("a tmux session named alpha is in the way, and Tardigrade does not know it as one it made for this "
                "session (it "
                f"holds `sleep`). Nothing in it was touched. Next step: give it another name with `{cmd}`, then run "
                "`tardigrade revive`.")
        self.assertIn("skip  Alpha Lane: not started: " + said, out)
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you, 1 not started: a tmux session of "
                      "that name is in the way\n", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["skipped"], rep.get("in_way"), rep.get("not_started")),
                         ([], ["Beta Lane"], ["Alpha Lane"], {"Alpha Lane": said}))
        self.assertEqual((len(self.launches()), self.pane("pane_pid")), (n, own))
        self.record_pass("in the way")                      # G1.20: the row stays where a revive looks for it
        census = self.tg("census")[1]
        self.assertIn("Running: 1 ", census)
        self.assertIn("      A revive did not start it: " + said, census)
        done = subprocess.run(["sh", "-c", cmd], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual((done.returncode, done.stdout + done.stderr), (0, ""))
        self.back(sid, "in the way", sessions=("alpha", "alpha-kept", "alpha-kept-2"))
        self.assertEqual(self.pane("pane_pid", "alpha-kept-2"), own, "the person's own session runs on, under its new name")

    def test_G5_19_a_place_where_claude_code_runs_under_another_id_is_not_started_beside_and_not_called_running(self):
        """the row's tmux session holds Claude Code under another session id (a conversation started new in the
        same place): nothing is started beside it, no step is asked of the person, and the next record follows it"""
        sid = self.one_up_one_down()
        self.lived_in("set -g base-index 1\n", None)
        self.other_up()
        new = self.start_lane("alpha", "Alpha Lane")
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.assertIn("skip  Alpha Lane: not started: its tmux session alpha runs Claude Code under another session id "
                      "(a conversation started new in the same place). Nothing was started beside it; the next record "
                      "follows that one.", out)
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you, 1 not started: a tmux session of "
                      "that name is in the way\n", out)
        self.assertEqual(len(self.launches()), n)
        rc, out = self.tg("record")
        self.assertEqual(sorted(r["session_id"] for r in self.table()["lanes"]), sorted([new, self.other]), out)
        self.assertIn("Running: 2 ", self.tg("census")[1])

    def test_G5_19_a_tmux_session_whose_mark_or_pane_gave_no_answer_is_not_touched_and_the_next_revive_reads_again(self):
        """The read at the place has three answers. Where the mark, the pane's state or its program could not be
        read, nothing is touched and nothing is counted as running; the line says so, and its step (the same
        command again) is taken."""
        sid = self.one_up_one_down()
        wrap = self.script("tmux-noanswer", '#!/bin/sh\nif [ -e "$HOME/tmux-noanswer" ]; then\n'
                           '  how=$(cat "$HOME/tmux-noanswer"); show=; mark=; pane=\n'
                           '  for a; do case "$a" in show-options) show=1;; @tardigrade_session) mark=1;; '
                           '*pane_dead*) pane=1;; esac; done\n'
                           '  [ "$how" = mark ] && [ -n "$show" ] && [ -n "$mark" ] && exit 1\n'
                           '  [ "$how" = pane ] && [ -n "$pane" ] && exit 0\n'
                           '  [ "$how" = program ] && [ -n "$pane" ] && { echo 0; exit 0; }\nfi\n'
                           f'exec {shlex.quote(TMUX)} "$@"\n')
        flag = os.path.join(self.home, "tmux-noanswer")
        for k, how in enumerate(("mark", "pane", "program")):
            if k:
                self.reboot()                               # another restart of the machine: a new failure
            why, out = self.not_back(how, cfg={"claude_command": "claude-nowhere", "tmux": wrap},
                                     conf="set -g base-index 1\n")
            self.cfg({"claude_command": os.path.join(self.bin, "claude")})      # the cause is cured
            self.record_pass(how)
            own, n = self.pane("pane_pid"), len(self.launches())
            with open(flag, "w") as f:
                f.write(how)
            rc, out = self.tg("revive")
            os.remove(flag)
            self.assertIn("skip  Alpha Lane: not started: a tmux session named alpha is there, and whether Tardigrade "
                          "made it for this session could not be read (tmux or the process list gave no answer). "
                          "Nothing in it was touched. Next step: run `tardigrade revive` again.", out, how)
            self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you, 1 not started: its tmux session "
                          "could not be read\n", out, how)
            self.assertEqual((len(self.launches()), self.pane("pane_pid")), (n, own), how)
            self.back(sid, how)

    def test_G5_19_a_run_that_was_stopped_after_a_start_that_failed_leaves_a_place_the_next_revive_starts_in(self):
        """A run that ends between a start and its give-up (a reboot, a kill) writes no give-up line and no report.
        The place it made carries the session's id, the record keeps the row, the census reads the cause from the
        place, and the next revive starts the session there."""
        sid = self.one_up_one_down()
        self.lived_in("set -g base-index 1\n", None)
        self.other_up()
        self.cfg({"claude_command": "claude-nowhere"})
        n = len(self.launches())
        rc, out = self.tg("revive", TARDIGRADE_FAKE_STOP_AT="claude_started:1")
        self.assertEqual(rc, 137, f"the planted stop was not reached: {out}")
        mark = self.state(os.path.join("launch", sid + ".mark"))
        self.until(lambda: os.path.exists(mark) and fread(mark) == "ended 127\n", "the start to have failed")
        place = self.pane("session_id")
        self.assertEqual(self.tmux(self.sock, "show-options", "-qv", "-t", "=alpha:", "@tardigrade_session")[1].strip(), sid)
        self.record_pass("a stopped run")
        census = self.tg("census")[1]
        self.assertIn("      A revive did not bring it back: `claude-nowhere` ended with exit code 127, not found", census)
        self.assertIn("1 of the sessions under DOWN: a revive was to start them", census)
        self.cfg({"claude_command": os.path.join(self.bin, "claude")})
        self.back(sid, "a stopped run")
        self.assertEqual((self.pane("session_id"), len(self.launches())), (place, n + 1), "started in that place")

    def test_G5_19_a_session_that_loaded_and_later_ends_alone_is_started_again_in_its_place(self):
        """A session a revive brought back, whose Claude Code ends later by itself (not by /exit) and leaves the
        shell: its tmux session is still the place made for it, with nothing running. A revive that brings the
        session back starts it there; no rename is asked of the person. Before the next record pass the row is
        still on the list a plain revive reads. After one, the record's own rule has moved a session that went
        alone to the down list ("its pane holds the shell it left"): a plain revive leaves it and its place alone,
        and `tardigrade revive --only NAME` starts it in its place."""
        sid, new = self.brought_back()
        place, own = self.pane("session_id"), self.pane("pane_pid")
        self.ends_alone(new)
        n = len(self.launches())
        rc, out = self.tg("revive")                         # within the five minutes: no record pass yet
        again = self.wait_launches(n + 1)[-1]
        self.assertEqual(again["argv"][again["argv"].index("--resume") + 1], sid, out)
        self.assertIn("revive: 1 started, 1 already running, 0 failed, 0 need you\n", out)
        self.assertNotIn("not started", out)
        time.sleep(1)
        self.assertEqual((self.pane("session_id"), len(self.launches()), self.mark()), (place, n + 1, sid),
                         "started once, in that place")
        self.record_pass("ended alone, started again")
        self.ends_alone(again)                              # once more, and this time the timer's record comes first
        rc, out = self.tg("record")
        self.assertEqual((self.names(), [(r["name"], r.get("ended")) for r in self.table()["down"]]),
                         (["Beta Lane"], [("Alpha Lane", "shell")]), out)
        self.assertIn("ended (its pane holds the shell it left)", self.tg("census")[1])
        own, n = self.pane("pane_pid"), len(self.launches())
        rc, out = self.tg("revive")
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you\n", out)
        self.assertEqual((len(self.launches()), self.pane("pane_pid"), self.mark()), (n, own, sid),
                         "a plain revive does not read the down list, and the place is as it was")
        rc, out = self.tg("revive", "--only", "Alpha Lane")
        self.assertEqual(rc, 0, out)
        third = self.wait_launches(n + 1)[-1]
        self.assertEqual(third["argv"][third["argv"].index("--resume") + 1], sid, out)
        self.assertNotIn("not started", out)
        time.sleep(1)
        self.assertEqual((self.pane("session_id"), len(self.launches()), self.mark()), (place, n + 1, sid),
                         "started once, in that place")
        self.record_pass("ended alone, brought back by name")

    def test_G5_19_sessions_that_were_brought_back_and_then_vanish_together_are_started_again_each_in_its_place(self):
        """A crash that ends several sessions at once and leaves their shells (a memory kill does this): the record
        keeps them as sessions to bring back, and the revive starts each in the tmux session made for it. None is
        "not started", and no rename is asked for a place the tool made itself. A fourth session runs on: with
        nothing running at all, the record keeps the record before it and says nothing of a crash."""
        ids = [self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta", "gamma")]
        ids, last = ids, self.start_lane("delta", "Delta Lane")
        self.tg("record", "--force")
        self.reboot()
        self.lived_in("set -g base-index 1\n", None)
        n = len(self.launches())
        rc, out = self.tg("revive")
        new = [x for x in self.wait_launches(n + 4)[n:] if x["argv"][x["argv"].index("--resume") + 1] != last]
        self.assertIn("revive: 4 started, 0 already running, 0 failed, 0 need you\n", out)
        self.assertEqual(len(new), 3)
        places = lambda: sorted(self.tmux(self.sock, "list-sessions", "-F", "#{session_name} #{session_id}")[1].split("\n"))
        marks = lambda: [self.mark(x) for x in ("alpha", "beta", "gamma")]
        before = places()
        self.assertEqual(marks(), ids)
        self.tg("record")
        for row in new:                                     # all three at once, no /exit
            self.kill(row["pid"])
        for name in ("alpha", "beta", "gamma"):
            self.until(lambda: self.pane("pane_current_command", name) == "bash", "the shell kept in its place")
        rc, out = self.tg("record")
        self.assertIn("3 of 4 recorded sessions vanished together", out)
        self.assertEqual((self.names(), self.names("down")),
                         (["Alpha Lane", "Beta Lane", "Delta Lane", "Gamma Lane"], []), out)
        n = len(self.launches())
        rc, out = self.tg("revive")
        again = self.wait_launches(n + 3)[n:]
        self.assertEqual(sorted(x["argv"][x["argv"].index("--resume") + 1] for x in again), sorted(ids), out)
        self.assertIn("revive: 3 started, 1 already running, 0 failed, 0 need you\n", out)
        self.assertNotIn("not started", out)
        time.sleep(1)
        self.assertEqual((places(), len(self.launches()), marks()), (before, n + 3, ids), "each once, each in its place")
        self.assertIn("Running: 4 ", self.tg("census")[1])

    def test_G5_19_a_tmux_session_whose_mark_names_another_session_is_not_this_sessions_place(self):
        """The mark names the session it was made for, and the read compares it. A tmux session of the row's name
        whose mark names another session (made for a conversation that is not the row's) is not started in: it is
        not touched, the session is counted as not started, and the step is the printed rename, taken as printed."""
        sid = self.one_up_one_down()
        self.lived_in("set -g base-index 1\n", None)
        self.other_up()
        other = str(uuid.uuid4())
        self.assertEqual(self.tmux(self.sock, "new-session", "-d", "-s", "alpha", "-c", self.work)[0], 0)
        self.assertEqual(self.tmux(self.sock, "set-option", "-t", "=alpha:", "@tardigrade_session", other)[0], 0)
        self.until(lambda: self.pane("pane_current_command") in ("bash", "sh", "dash", "zsh"), "its shell")
        own, n = self.pane("pane_pid"), len(self.launches())
        rc, out = self.tg("revive")
        cmd = f"{TMUX} -S {self.sock} rename-session -t '=alpha' alpha-kept"
        said = ("a tmux session named alpha is in the way, and Tardigrade does not know it as one it made for this "
                f"session (it holds a shell). Nothing in it was touched. Next step: give it another name with `{cmd}`, "
                "then run `tardigrade revive`.")
        self.assertIn("skip  Alpha Lane: not started: " + said, out)
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you, 1 not started: a tmux session of "
                      "that name is in the way\n", out)
        self.assertEqual((len(self.launches()), self.pane("pane_pid"), self.mark()), (n, own, other))
        self.record_pass("made for another session")
        self.assertIn("      A revive did not start it: " + said, self.tg("census")[1])
        done = subprocess.run(["sh", "-c", cmd], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual((done.returncode, done.stdout + done.stderr), (0, ""))
        self.back(sid, "made for another session", sessions=("alpha", "alpha-kept"))
        self.assertEqual((self.pane("pane_pid", "alpha-kept"), self.mark("alpha-kept")), (own, other),
                         "the other place runs on under its new name, with its own mark")

    def test_G5_19_a_program_started_in_the_place_of_a_session_that_loaded_and_ended_is_named_and_never_ended(self):
        """With the mark on after the session loaded: its Claude Code ends alone, and the person starts a program in
        the shell it left. "Holds only a shell" is read from the process list too, so the program is named, is not
        ended, and the session is not started over it. The printed step is taken: once the program has ended, the
        same `tardigrade revive` starts the session in that place."""
        sid, new = self.brought_back()
        place = self.pane("session_id")
        self.ends_alone(new)
        self.tmux(self.sock, "send-keys", "-t", "=alpha:", "-l", "sleep 300")       # the person, in their own window
        self.tmux(self.sock, "send-keys", "-t", "=alpha:", "Enter")
        own = self.pane("pane_pid")
        kids = lambda: subprocess.run(["pgrep", "-x", "sleep", "-P", own], capture_output=True, text=True).stdout.split()
        self.until(lambda: len(kids()) == 1, "the person's program to run under the shell")
        pid = int(kids()[0])
        n, began = len(self.launches()), start_time(pid)
        rc, out = self.tg("revive")
        look = f"`{TMUX} -S {self.sock} attach -t '=alpha'`"
        self.assertIn("skip  Alpha Lane: not started: its tmux session alpha holds a running program (`sleep`), and "
                      f"Tardigrade does not end it. Next step: end that program in its window ({look}), then run "
                      "`tardigrade revive`.", out)
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you, 1 not started: a tmux session of "
                      "that name is in the way\n", out)
        self.assertEqual((len(self.launches()), self.pane("pane_pid"), start_time(pid), self.mark()),
                         (n, own, began, sid), "the person's program is not touched")
        self.record_pass("a program in its place")
        self.kill(pid)                                      # the step: the person ends that program
        self.until(lambda: not kids(), "the program to have ended")
        self.back(sid, "a program in its place")
        self.assertEqual((self.pane("session_id"), len(self.launches())), (place, n + 1), "started in that place")

    def test_G5_19_a_session_its_owner_ended_is_not_started_again_in_its_place_though_the_place_keeps_its_mark(self):
        """With the mark on after the session loaded: its owner ends it with /exit. The record's own rule holds: it
        is over, a revive starts nothing, and the shell it left in the place made for it is not touched."""
        sid, new = self.brought_back()
        self.tg("record")
        seen = [r for r in self.table()["lanes"] if r["session_id"] == sid][0]["last_seen"]
        self.conversation(sid, seen + 2)                    # its owner typed /exit after the record saw it running
        self.ends_alone(new)
        time.sleep(2)
        own, n = self.pane("pane_pid"), len(self.launches())
        self.assertEqual(self.mark(), sid)
        rc, out = self.tg("revive")                         # before the record has written it down as ended
        self.assertIn("skip  Alpha Lane: its owner ended it with /exit", out)
        rc, out = self.tg("record")
        self.assertEqual((self.names(), [(r["name"], r.get("ended")) for r in self.table()["down"]]),
                         (["Beta Lane"], [("Alpha Lane", "exit")]), out)
        rc, out = self.tg("revive")                         # and after
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you\n", out)
        time.sleep(1)
        self.assertEqual((len(self.launches()), self.pane("pane_pid"), self.mark()), (n, own, sid),
                         "nothing was started, and its place is as it was")

    def test_G5_19_claude_code_under_another_id_in_the_place_of_a_session_that_loaded_gets_no_second_copy(self):
        """With the mark on after the session loaded: its Claude Code ends alone, and the person starts Claude Code
        new in the shell it left (another session id). Nothing is started beside it, no step is asked of the
        person, and the next record follows the one that runs."""
        sid, new = self.brought_back()
        self.ends_alone(new)
        other, n = str(uuid.uuid4()), len(self.launches())
        self.tmux(self.sock, "send-keys", "-t", "=alpha:", "-l", f"FAKE_SID={other} FAKE_NAME='Alpha Lane' claude")
        self.tmux(self.sock, "send-keys", "-t", "=alpha:", "Enter")
        self.assertEqual(self.wait_launches(n + 1)[-1]["sid"], other)
        self.assertEqual(self.mark(), sid, "the place still names the session it was made for")
        rc, out = self.tg("revive")
        self.assertIn("skip  Alpha Lane: not started: its tmux session alpha runs Claude Code under another session id "
                      "(a conversation started new in the same place). Nothing was started beside it; the next record "
                      "follows that one.", out)
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you, 1 not started: a tmux session of "
                      "that name is in the way\n", out)
        time.sleep(1)
        self.assertEqual(len(self.launches()), n + 1, "no second copy")
        rc, out = self.tg("record")
        self.assertEqual(sorted(r["session_id"] for r in self.table()["lanes"]), sorted([other, self.other]), out)
        self.assertIn("Running: 2 ", self.tg("census")[1])
        rc, out = self.tg("revive")
        self.assertIn("revive: 0 started, 2 already running, 0 failed, 0 need you\n", out)
        self.assertEqual(len(self.launches()), n + 1, "and none after the record")

    def test_G5_19_uninstall_says_how_many_tmux_sessions_keep_the_mark_and_says_so_when_it_could_not_read(self):
        """The mark stays on a tmux session the tool made, and uninstall touches no session. A later setup reads the
        mark, so it is a thing of the tool's left on the computer, and uninstall's last words name it: how many
        running tmux sessions carry it, and that it ends with each. Three answers: a count; no word of it where no
        session carries it; and where tmux could not be read, that is said, never taken for none."""
        # systemd answers for the units (not there, not running), so each uninstall here ends as "off"
        self.systemd_off()
        rc, out = self.tg("uninstall")                      # no tmux server at all: none carries it
        self.assertEqual(rc, 0, out)
        self.assertIn("Running sessions were not touched.\n", out)
        self.assertNotIn("mark", out)
        sid, row = self.brought_back()                      # two tmux sessions run; the tool made one of them
        self.assertEqual(self.mark("beta"), "", "the session the person started carries none")
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("Running sessions were not touched.\n1 tmux session that Tardigrade made still carries its mark "
                      "(the tmux option @tardigrade_session: the id of the session it was made for). Uninstall "
                      "touches no session, so the mark is not taken off; it ends with that tmux session.\n", out)
        self.assertEqual((self.mark(), running(row["pid"])), (sid, True), "nothing of the session was touched")
        # tmux gives no answer about a server that is there: said as that, and no count beside it
        self.cfg({"tmux": self.script("tmux-broken", "#!/bin/sh\necho 'lost server' >&2\nexit 1\n")})
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("Running sessions were not touched.\nWhether a running tmux session still carries Tardigrade's "
                      "mark (the tmux option @tardigrade_session) could not be read: the tmux server at "
                      f"{self.sock} did not list its sessions (tmux ended with code 1). Uninstall touches no session; "
                      "a mark ends with its tmux session.\n", out)
        self.assertNotIn("that Tardigrade made", out)
        self.cfg({"tmux": TMUX})
        self.assertEqual((self.mark(), running(row["pid"])), (sid, True))

    def test_G5_19_the_line_names_the_start_files_of_the_shell_the_tool_starts_and_no_file_where_it_has_no_list(self):
        """The line that sends a person to a start file names the files of the shell the tool itself starts the
        session with (its `shell` setting, else the login shell): the ones zsh reads for zsh, never a ~/.bashrc
        that a person on a Mac does not have; and no file at all for a shell it has no list for. The stand-in
        shells end at once, so this reads the words only; the step is taken by the tests with a real bash."""
        self.one_up_one_down()
        for k, (shell, files) in enumerate((
                ("zsh", "the shell's start files (~/.zshenv, ~/.zprofile, ~/.zshrc, ~/.zlogin)"),
                ("fish", "the shell's own start files"))):
            if k:
                self.reboot()                               # another restart of the machine: a new failure
            self.script(shell, "#!/bin/sh\nexit 0\n")
            why, out = self.not_back(shell, cfg={"shell": shell}, conf="set -g base-index 1\n")
            self.assertEqual(why, "it ended at once and tmux removed it: the shell ended before it read the session's "
                             f"start file. The usual cause is a line in {files} that ends the shell, or that runs a "
                             "program in the shell's place which ends (an `exit`, an `exec tmux`). Next step: in that "
                             "file, keep that line from running inside tmux, then run `tardigrade revive`.", shell)
            self.assertNotIn("bashrc", out + self.tg("census")[1], shell)

    def test_G5_19_a_program_in_the_pane_is_named_by_its_name_only_never_by_its_command_line(self):
        """A command line can carry a key or a token. The lines that name a program in the pane take its name from
        tmux (the pane's program) or from the name column of the process list, never its arguments: a stand-in
        started with a made-up secret in its arguments, with and without exec, is named, and no line, no log and no
        file of the tool holds the secret. The same for the command that starts Claude Code: a setting that begins
        with VAR=value is named by its program."""
        secret = "tok-MADE-UP-" + uuid.uuid4().hex
        self.script("holder", f"#!{sys.executable}\nimport time\ntime.sleep(300)\n")
        sid = self.one_up_one_down()
        plain = "set -g base-index 1\n"

        def nowhere(said, case, but=()):
            self.assertNotIn(secret, said, case)
            for d, dirs, files in os.walk(self.home):
                for n in files:
                    p = os.path.join(d, n)
                    if os.path.relpath(p, self.home) in but or os.path.islink(p) or not os.path.isfile(p):
                        continue
                    with open(p, "rb") as f:
                        self.assertNotIn(secret.encode(), f.read(), f"{case}: {p}")

        def said():
            return "".join(self.tg(*a)[1] for a in (("census",), ("census", "--json"), ("report",)))

        for k, rc in enumerate((f"exec holder --token {secret}\n", f"holder --token {secret}\n")):
            if k:
                self.reboot()                               # another restart of the machine: a new failure
            why, out = self.not_back(f"a program with arguments, {k}", rc=rc, conf=plain)
            own = self.pane("pane_pid")
            kids = subprocess.run(["pgrep", "-P", own], capture_output=True, text=True).stdout.split()
            pid = int((kids or [own])[0])
            self.assertIn(secret, cmdline_of(pid), "the stand-in runs with the secret in its arguments")
            m = re.search(r"started another program in the pane \(`([^`]+)`\) and it has not ended", why)
            self.assertTrue(m and m.group(1) in ("holder", os.path.basename(sys.executable)), why)
            rc2, again = self.tg("revive")
            self.assertIn(f"holds a running program (`{m.group(1)}`), and Tardigrade does not end it.", again)
            self.record_pass(f"a program with arguments, {k}, met again")
            self.assertIsNotNone(start_time(pid), "and it was not ended")
            nowhere(out + again + said(), f"a program with arguments, {k}", but=(".bashrc",))
        self.reboot()
        why, out = self.not_back("a start command that begins with VAR=value",
                                 cfg={"claude_command": f"TOKEN={secret} claude-nowhere"}, conf=plain)
        self.assertTrue(why.startswith("`claude-nowhere` ended with exit code 127, not found: "), why)
        # two files hold it and must: the person's own setting, and the session's start file made from it
        dry = self.tg("revive", "--dry-run", "--all")[1]
        self.assertIn("   TOKEN=(set) claude-nowhere ", dry)
        nowhere(out + said() + dry, "a start command that begins with VAR=value",
                but=(os.path.join(".config", "windstorm-tardigrade", "config.json"),
                     os.path.relpath(self.state(os.path.join("launch", sid + ".sh")), self.home)))

    # ----- G1.20 (T103): the record keeps a row that a revive was to start and that was not seen loaded -----
    KEPT = ("record: Alpha Lane: a revive was to start it and it has not been seen running since. It stays on the list "
            "a revive reads: `tardigrade revive` tries it again; `tardigrade record --force` moves every session that "
            "is not running to the down list.")
    KEPT_BELOW = ("1 of the sessions under DOWN: a revive was to start them and they were not seen running since. They "
                  "stay on the list a revive reads: `tardigrade revive` tries them again. To let them go: `tardigrade "
                  "record --force` (it moves every session that is not running to the down list).")

    def test_G1_20_a_session_whose_start_failed_stays_on_the_list_a_revive_reads_until_it_runs_or_the_person_lets_it_go(self):
        """T103. Seen on a stock machine with 0.1.10.1: with another session up, the timer's first record after a
        start that failed moved the row to the down list, and no revive looked at it again. Now the row stays; no
        clock ends that; and the one way to let it go, printed under the table, is taken."""
        sid = self.one_up_one_down()
        why, out = self.not_back("not found", cfg={"claude_command": "claude-nowhere"}, conf="set -g base-index 1\n")
        row = lambda: next(r for r in self.table()["lanes"] if r["session_id"] == sid)
        self.assertEqual((self.logged(self.KEPT), bool(row().get("not_started_since")), row().get("not_started_why")),
                         (1, True, why))
        self.record_pass("a second pass")
        self.assertEqual(self.logged(self.KEPT), 1, "said once, not at every pass")
        self.assertIn(self.KEPT_BELOW, self.tg("census")[1])
        # no clock: thirty days on, the row is where it was. The kept times are moved, never the calendar.
        t = self.table()
        for r in t["lanes"]:
            if r["session_id"] == sid:
                r["not_started_since"] -= 30 * 86400
                r["last_seen"] -= 30 * 86400
        jdump(t, os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json"))
        self.record_pass("thirty days on")
        census = self.tg("census")[1]
        self.assertIn(self.KEPT_BELOW, census)
        self.assertIn("      A revive did not bring it back: " + why, census)
        # the way to let it go, as printed
        n = len(self.launches())
        rc, out = self.tg("record", "--force")
        self.assertEqual((rc, self.names(), self.names("down")), (0, ["Beta Lane"], ["Alpha Lane"]), out)
        rc, out = self.tg("revive")
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you\n", out)
        self.assertEqual(len(self.launches()), n)
        self.assertNotIn("a revive was to start them", self.tg("census")[1])

    def test_G1_20_a_session_its_owner_ended_is_not_kept_and_a_row_the_progress_file_does_not_name_goes_down_as_before(self):
        """T103, the two ways a row still leaves by itself: its owner's /exit, read as before; and a session that
        simply is not running, of which no revive's progress file says that it was being started"""
        sid = self.one_up_one_down()
        why, out = self.not_back("not found", cfg={"claude_command": "claude-nowhere"}, conf="set -g base-index 1\n")
        self.conversation(sid, time.time())                 # its owner ended it, after the revive gave up
        rc, out = self.tg("record")
        down = self.table()["down"]
        self.assertEqual((self.names(), [(r["name"], r.get("ended")) for r in down]),
                         (["Beta Lane"], [("Alpha Lane", "exit")]), out)
        self.assertEqual([k for r in down for k in r if k.startswith("not_started")], [])
        # a row that no progress file names: started by hand, seen by a record, then gone by itself
        self.assertTrue(os.path.exists(self.state("progress.json")))
        self.start_lane("gamma", "Gamma Lane")
        rc, out = self.tg("record")
        self.assertEqual(self.names(), ["Beta Lane", "Gamma Lane"], out)
        self.kill(self.launches()[-1]["pid"])
        self.until(lambda: self.tmux(self.sock, "has-session", "-t", "=gamma")[0] != 0, "its window to go")
        rc, out = self.tg("record")
        self.assertEqual((self.names(), sorted(r["name"] for r in self.table()["down"])),
                         (["Beta Lane"], ["Alpha Lane", "Gamma Lane"]), out)

    def test_G1_20_a_progress_file_that_could_not_be_read_keeps_the_rows_it_might_name_and_says_so(self):
        """T103, three answers: where the last revive's progress file is there and cannot be read, which sessions it
        was starting is not known. A row that is not running is then kept, never silently dropped, and the log
        says so once; a revive starts it."""
        sid = self.one_up_one_down()
        self.lived_in("set -g base-index 1\n", None)
        self.other_up()
        os.makedirs(self.state(""), exist_ok=True)
        with open(self.state("progress.json"), "w") as f:
            f.write('{"pid": 1, "sessions": {')
        said = ("record: the last revive's progress file could not be read, so which sessions it was starting is not "
                "known. These are not running and stay on the list a revive reads: Alpha Lane")
        out = self.record_pass("an unread progress file")
        self.assertIn(said, out)
        self.record_pass("a second pass")
        self.assertEqual(self.logged(said), 1, "said once")
        self.assertIn(self.KEPT_BELOW, self.tg("census")[1])
        self.back(sid, "an unread progress file")

    def test_G2_2_the_revived_session_is_told_what_happened(self):
        sid = self.recorded_then_rebooted()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        typed = self.wait_typed(1)
        self.assertEqual([t["sid"] for t in typed], [sid], out)
        self.assertIn("this machine restarted", typed[0]["text"])
        self.assertIn("background is gone", typed[0]["text"])
        self.assertEqual(jload(self.state("last-revive.json"))["noted"], ["Alpha Lane"])

    def test_G2_3_wake_idle_types_nothing(self):
        self.cfg({"wake": "idle"})
        sid = self.recorded_then_rebooted()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertEqual(self.wait_launches(n + 1)[-1]["argv"][-1], sid)
        time.sleep(2)
        self.assertEqual(self.typed(), [])

    def test_G2_51_a_rows_login_is_never_emptied_by_a_failed_read_and_never_changed_without_a_line(self):
        """T57: three sessions were ended and started again by hand on the machine's own login; the record followed
        what ran and no line said that a row's account had changed. And in the code a read of a session's environment
        that failed was written down as "no account, no token": one failed pass would have emptied a row for good."""
        import shlex
        self.script("acctwrap", '#!/bin/bash\necho "$@" >> "$HOME/acctwrap.log"\n[ "$1" = use ] || exit 2\n'
                                'tok="tok-$2"; [ -f "$HOME/wraptoken-$2" ] && tok=$(cat "$HOME/wraptoken-$2")\nshift 2\n'
                                'CLAUDE_CODE_OAUTH_TOKEN="$tok" exec claude "$@"\n')
        self.cfg({"account_env": "ACCT_NAME", "account_command": "acctwrap use {account}"})
        a = self.start_lane("alpha", "Alpha Lane", extra_env={"ACCT_NAME": "sam", "CLAUDE_CODE_OAUTH_TOKEN": "tok-sam"})
        b = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        login = ("account", "token_keys", "token_print", "config_dir", "carry_env")

        def row(sid):
            return next((r for r in self.table()["lanes"] if r["session_id"] == sid), None)

        def facts(sid):
            return {k: row(sid)[k] for k in login}

        def prints(tok):
            return hashlib.sha256(("tardigrade:CLAUDE_CODE_OAUTH_TOKEN=" + tok).encode()).hexdigest()[:8]

        def pid_of(sid):
            return [x["pid"] for x in self.launches() if x["sid"] == sid][-1]
        first = facts(a)
        self.assertEqual((first["account"], first["token_print"], row(a).get("env_read")), ("sam", prints("tok-sam"), True))
        beta = facts(b)

        # 1. the read of every session's environment fails for a pass: nothing is emptied, and it is said once
        rc, out = self.tg("record", TARDIGRADE_FAKE_ENV_UNREAD=1)
        self.assertEqual(rc, 0, out)
        self.assertEqual((facts(a), facts(b)), (first, beta), out)
        self.assertIn("record: Alpha Lane: its environment could not be read; its login is kept as recorded "
                      "(account sam), nothing is emptied", out)
        self.assertIn("record: Beta Lane: its environment could not be read; its login is kept as recorded "
                      "(this machine's own login)", out)
        since = row(a)["env_unread_since"]
        self.assertTrue(since)
        c = self.start_lane("gamma", "Gamma Lane", extra_env={"ACCT_NAME": "tom", "CLAUDE_CODE_OAUTH_TOKEN": "tok-tom"})
        rc, out = self.tg("record", TARDIGRADE_FAKE_ENV_UNREAD=1)
        self.assertNotIn("Alpha Lane: its environment could not be read", out)          # said when it begins, once
        self.assertEqual((facts(a), row(a)["env_unread_since"]), (first, since))
        self.assertIn("record: Gamma Lane: its environment could not be read and it has no row yet; nothing is "
                      "written down for it until it can be read", out)
        self.assertIsNone(row(c), "a session whose login is not known is not written down as one with none")
        rc, out = self.tg("record")                                                       # the read works again
        self.assertEqual((facts(a), row(c)["account"]), (first, "tom"), out)
        self.assertNotIn("env_unread_since", row(a))
        self.assertNotIn("Alpha Lane", out)

        # 2. a revive starts Alpha from its row, and the account command gives another login
        self.reboot()
        with open(os.path.join(self.home, "wraptoken-sam"), "w") as f:
            f.write("tok-other")
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = {x["sid"]: x for x in self.wait_launches(n + 3)[n:]}
        self.assertEqual(new[a]["seen"].get("ACCT_NAME"), "sam")            # the label is the row's: the start file sets it
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((sorted(rep["started"]), rep["needs_you"]), (["Beta Lane", "Gamma Lane"], ["Alpha Lane"]), out)
        self.assertIn("needs you  Alpha Lane: it came back on a different login than it had before (the account "
                      "command gave another login, or none). It is idle and nothing was typed. Next step: look at the "
                      f"login it shows; if that is the right one, `record --accept {a}` makes its row follow it", out)
        self.assertEqual([x for x in self.typed() if x["sid"] == a], [])
        rc, out = self.tg("record")
        self.assertEqual(facts(a), first, "the row of a session a revive started keeps its login: " + out)
        seen = row(a)["login_seen"]
        self.assertEqual((seen["who"], seen["token_print"]), ("tardigrade", prints("tok-other")))
        self.assertIn("record: Alpha Lane: a revive started it from its row and it runs on another login: the account "
                      "command gave another login, or none (was account sam, now account sam with another login "
                      f"token). Its row keeps the recorded one. Next step: look at the login it shows; if that is the "
                      f"right one, `record --accept {a}` makes its row follow it", out)
        rc, out = self.tg("record")
        self.assertNotIn("Alpha Lane", out)                                             # once, not every five minutes
        self.assertEqual((facts(a), row(a)["login_seen"]), (first, seen))
        rc, out = self.tg("census")
        self.assertIn("Login differs: Alpha Lane is recorded on account sam and runs on account sam with another "
                      "login token", out)
        self.assertIn(f"A revive started it from its row: the account command gave another login, or none. Its row "
                      f"keeps the recorded one. Next step: look at the login it shows; if that is the right one, "
                      f"`record --accept {a}` makes its row follow it.", out)
        rc, js = self.tg("census", "--json")
        self.assertEqual([(u["session_id"], u["who"]) for u in json.loads(js)["login_differs"]], [(a, "tardigrade")])

        # 3. a person's word for that one session; a wrong name changes nothing
        was = self.table()["lanes"]
        for wrong, said in (("No Such Lane", "no session on the record is named 'No Such Lane'"),
                            (None, "give the name or the id of one session on the record")):
            rc, out = self.tg("record", "--accept", *([wrong] if wrong else []))
            self.assertEqual(rc, 1, out)
            self.assertIn("record --accept: " + said, out)
            self.assertIn("Nothing was changed.", out)
            self.assertEqual(self.table()["lanes"], was)
        rc, out = self.tg("record", "--accept", "Alpha Lane")
        self.assertEqual(rc, 0, out)
        self.assertIn("record: Alpha Lane: its row takes the login it runs on now, on a person's word (was account "
                      "sam, now account sam with another login token)", out)
        self.assertEqual((row(a)["account"], row(a)["token_print"]), ("sam", prints("tok-other")))
        self.assertNotIn("login_seen", row(a))
        self.assertEqual((facts(b), row(c)["account"]), (beta, "tom"), "that session only")
        self.assertNotIn("Login differs", self.tg("census")[1])
        rc, out = self.tg("record", "--accept", a)
        self.assertEqual(rc, 0, out)
        self.assertIn("record --accept: Alpha Lane: its row already has the login it runs on (account sam); nothing "
                      "to take", out)

        # 4. its owner ends Alpha and starts it again in the shell that was kept, on the machine's own login
        self.kill(pid_of(a))
        gone_soon(lambda: running(pid_of(a)))
        n = len(self.launches())
        end = time.time() + WAIT
        while len(self.launches()) == n and time.time() < end:      # typed again until the kept shell has taken it
            self.tmux(self.sock, "send-keys", "-t", "=alpha:", "-l",
                      f"env -u ACCT_NAME {shlex.quote(os.path.join(self.bin, 'claude'))} --resume {a}")
            self.tmux(self.sock, "send-keys", "-t", "=alpha:", "Enter")
            time.sleep(1.5)
        hand = self.wait_launches(n + 1)[n]
        self.assertEqual((hand["sid"], hand["seen"].get("ACCT_NAME")), (a, None))
        rc, out = self.tg("record")
        self.assertIn("record: Alpha Lane: its owner started it again on another login", out)
        self.assertIn(": was account sam, now this machine's own login. The record follows it, and a revive starts "
                      "it that way", out)
        self.assertEqual((row(a)["account"], row(a)["token_print"], "login_seen" in row(a)), ("", "", False), out)
        rc, out = self.tg("census")
        self.assertIn("Login changed: Alpha Lane was started again by its owner on another login", out)
        self.assertIn("was account sam, now this machine's own login. The record follows it", out)
        rc, out = self.tg("record")
        self.assertNotIn("Alpha Lane", out)
        wrapped = len(fread(os.path.join(self.home, "acctwrap.log")).splitlines())
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_BOOT_ID="boot-b")
        new = {x["sid"]: x for x in self.wait_launches(n + 3)[n:]}
        self.assertNotIn("ACCT_NAME", new[a]["seen"])                 # the way its owner last started it
        self.assertEqual(len(fread(os.path.join(self.home, "acctwrap.log")).splitlines()), wrapped + 1, "Gamma only")
        self.assertIn("Alpha Lane", jload(self.state("last-revive.json"))["started"])
        self.assertNotIn("Login changed", self.tg("census", TARDIGRADE_FAKE_BOOT_ID="boot-b")[1])   # until the next restart

        # 5. a row an older version wrote (it did not say whether a read had worked) takes what is read, with a line
        t = self.table()
        for r in t["lanes"]:
            if r["session_id"] == b:
                r.pop("env_read")
                r["account"] = "zed"
        jdump(t, os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json"))
        rc, out = self.tg("record", TARDIGRADE_FAKE_BOOT_ID="boot-b")
        self.assertIn("record: Beta Lane: its login is taken as read now (was account zed, now this machine's own "
                      "login); its row was written by an older version", out)
        self.assertEqual((row(b)["account"], row(b)["env_read"]), ("", True))

        # 6. who started a running Claude: each of the three answers, and the two that must not be mistaken
        tg = self.load_tool()
        start = ". " + shlex.quote(os.path.join(tg.launch_dir(), a + ".sh"))
        me = os.getuid()
        P = {50: (40, me, "claude"), 40: (30, me, "bash"), 30: (20, me, "bash"), 20: (1, me, "tmux: server")}
        for above, want in (({40: ["bash", "-ic", start]}, "tardigrade"),
                            ({40: ["zsh", "-lic", start]}, "tardigrade"),
                            ({40: ["bash", "/x/acctwrap", "use", "sam"], 30: ["bash", "-ic", start]}, "tardigrade"),
                            ({40: ["bash", "-l"]}, "owner"),
                            # the kept login shell, under a shell that only carries the start line as text
                            ({40: ["bash", "-l"], 30: ["sh", "-c", "bash -ic " + shlex.quote(start)]}, "owner"),
                            ({40: ["bash", "-ic", start.replace(a, b)]}, "owner"),      # another session's start file
                            ({40: []}, "unknown"),
                            ({40: [], 30: ["bash", "-ic", start]}, "tardigrade")):
            argv = {30: ["bash", "-l"], 20: ["tmux", "new-session"], **above}
            self.assertEqual(tg.started_by(50, a, P, argv=lambda q: argv.get(q, [])), want, above)

    def test_G2_53_a_revive_after_one_stopped_part_way_starts_each_session_at_most_once(self):
        # T85: a revive stopped by time (SIGTERM after 0.2 to 5 s) and run again gave each session one start, 8 of 8;
        # a stop by time does not reach each point on purpose. Here the run ends itself at planted points, at once
        # and with nothing tidied, and the next run is counted: starts, live processes and panes of each session.
        names = ("alpha", "beta", "gamma")
        sids = [self.start_lane(n, n.title() + " Lane") for n in names]
        self.tg("record", "--force")
        # after the 2nd waiting screen is made (no Claude yet); right after the 2nd Claude took its screen's pane;
        # after the 2nd start was counted; between the 1st and the 2nd session
        for point in ("screen_made:2", "respawned:2", "claude_started:2", "between:1"):
            self.reboot()
            n = len(self.launches())
            rc, out = self.tg("revive", TARDIGRADE_FAKE_STOP_AT=point)
            self.assertEqual(rc, 137, f"{point}: the planted stop was not reached: {out}")
            rc, out = self.tg("revive")
            self.wait_launches(n + 3)
            time.sleep(3)
            new = self.launches()[n:]
            self.assertEqual(sorted(x["sid"] for x in new), sorted(sids), f"{point}: a session started twice, or not: {out}")
            for x in new:
                os.kill(x["pid"], 0)                        # each one's Claude is alive (raises when it is not)
            rc, got = self.tmux(self.sock, "list-panes", "-a", "-F", "#{session_name}")
            self.assertEqual(sorted(got.split()), sorted(names), f"{point}: one pane a session, no session more: {out}")
            for nm in names:
                self.assertEqual(self.tmux(self.sock, "show-options", "-qv", "-t", f"={nm}:", "@tardigrade_waiting")[1].strip(),
                                 "", f"{point}: {nm} is still marked as a waiting screen")
            self.tg("record", "--force")
        # tests only: without the suite's two keys the setting is not read at all
        src = fread(SCRIPT)
        body = src[src.index("def planted_stop(point):"):src.index("CFG = {")]
        self.assertLess(body.index("if not TESTING:"), body.index('os.environ.get("TARDIGRADE_FAKE_STOP_AT"'))

    def test_G2_4_never_a_second_copy(self):
        self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.assertIn("already running", out)
        time.sleep(3)
        self.assertEqual(len(self.launches()), n)

    def test_G2_5_boot_revive_runs_once_per_boot_and_only_after_a_boot(self):
        self.recorded_then_rebooted()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=999999)
        self.assertIn("up too long", out)
        self.assertEqual(len(self.launches()), n)
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        self.assertTrue(os.path.exists(self.state("revived-boot-a")))
        self.reboot()
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertIn("already ran", out)
        self.assertEqual(len(self.launches()), n + 1)

    def test_G2_6_dry_run_starts_nothing(self):
        self.recorded_then_rebooted()
        n = len(self.launches())
        rc, out = self.tg("revive", "--dry-run")
        self.assertIn("start Alpha Lane", out)
        time.sleep(3)
        self.assertEqual(len(self.launches()), n)

    def test_G2_7_a_revived_session_gets_a_clean_environment(self):
        self.recorded_then_rebooted()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, CLAUDE_CODE_CHILD_SESSION=1,
                CLAUDE_CODE_SESSION_ID="someone-else", CLAUDECODE=1)
        new = self.wait_launches(n + 1)[-1]
        self.assertFalse(new["child"])
        self.assertEqual(new["claude_env"], [])
        self.assertIn("Alpha Lane: up (child marker absent)", fread(self.state("tardigrade.log")))

    def test_G2_8_a_machine_wide_login_token_comes_back_and_is_checked(self):
        # every session has the same token, and the person's shell supplies it again: an ordinary revive
        with open(os.path.join(self.home, ".bashrc"), "w") as f:
            f.write("export CLAUDE_CODE_OAUTH_TOKEN=tok-abc\n")
        self.start_lane("alpha", "Alpha Lane", extra_env={"CLAUDE_CODE_OAUTH_TOKEN": "tok-abc"})
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        self.assertIn("this machine restarted", self.wait_typed(1)[0]["text"])
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["needs_you"]), (["Alpha Lane"], []), out)
        self.assertIn("same login as before", out)

    def test_G2_11_a_pinned_session_comes_back_silent_and_a_changed_login_is_reported(self):
        a = self.start_lane("alpha", "Alpha Lane")
        b = self.start_lane("beta", "Beta Lane", extra_env={"CLAUDE_CODE_OAUTH_TOKEN": "tok-pinned"})
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 2)
        typed = self.wait_typed(1)
        time.sleep(2)
        self.assertEqual([t["sid"] for t in self.typed()], [a])   # the note for the ordinary session only;
        self.assertIn("this machine restarted", typed[0]["text"]) # nothing is typed where the login changed
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["needs_you"]), (["Alpha Lane"], ["Beta Lane"]), out)
        self.assertIn("different login", out)
        # G2.51 (T57): the line says why, and the one next step; the row keeps the login it had until a person says
        self.assertIn("needs you  Beta Lane: it came back on a different login than it had before (its row has a login "
                      "token and no account label, and a revive cannot give such a login back). It is idle and nothing "
                      f"was typed. Next step: look at the login it shows; if that is the right one, `record --accept {b}`"
                      " makes its row follow it; if not, close it and start it your usual way.", out)
        was = next(r for r in self.table()["lanes"] if r["session_id"] == b)["token_print"]
        rc, out = self.tg("record")
        self.assertEqual(next(r for r in self.table()["lanes"] if r["session_id"] == b)["token_print"], was, out)
        self.assertIn("Login differs: Beta Lane is recorded on a login token in its environment and runs on this "
                      "machine's own login", self.tg("census")[1])
        rc, out = self.tg("record", "--accept", b)
        self.assertEqual((rc, next(r for r in self.table()["lanes"] if r["session_id"] == b)["token_print"]), (0, ""), out)
        self.assertNotIn("Login differs", self.tg("census")[1])

    def test_G2_9_two_revives_at_once_do_not_both_run(self):
        self.recorded_then_rebooted()
        os.makedirs(self.state("lock"))
        with open(self.state(os.path.join("lock", "owner")), "w") as f:
            f.write(f"boot-a {os.getpid()}\n")          # taken in this boot, by a process that is alive
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.assertIn("another revive is running", out)
        self.assertEqual(len(self.launches()), n)

    def test_G2_31_a_lock_left_by_a_revive_that_a_reboot_cut_off_does_not_block_the_next_one(self):
        self.recorded_then_rebooted()
        os.makedirs(self.state("lock"))
        with open(self.state(os.path.join("lock", "owner")), "w") as f:
            f.write(f"boot-before {os.getpid()}\n")     # the lock is from the boot before this one
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertIn("taking over", out)
        self.wait_launches(n + 1)
        self.assertEqual(jload(self.state("last-revive.json"))["started"], ["Alpha Lane"])
        self.assertFalse(os.path.exists(self.state("lock")))
        self.reboot()
        os.makedirs(self.state("lock"))
        with open(self.state(os.path.join("lock", "owner")), "w") as f:
            f.write("boot-a 999999999\n")                # same boot, but the process that took it is gone
        rc, out = self.tg("revive")
        self.assertIn("taking over", out)
        self.reboot()
        os.makedirs(self.state("lock"))                   # a lock with no owner in it, made before this boot
        old = time.time() - 3600
        os.utime(self.state("lock"), (old, old))
        rc, out = self.tg("revive", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertIn("taking over", out)
        os.makedirs(self.state("lock"))                   # and one made in this boot: that one holds
        rc, out = self.tg("revive", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertIn("another revive is running", out)

    def test_G2_10_only_brings_back_just_that_one(self):
        self.start_lane("alpha", "Alpha Lane")
        b = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--only", "Beta Lane")
        self.assertEqual(rc, 0, out)
        new = self.wait_launches(n + 1)
        time.sleep(3)
        self.assertEqual([x["sid"] for x in self.launches()[n:]], [b])
        rc, out = self.tg("revive", "--only", "No Such Lane")
        self.assertEqual(rc, 1)

    def test_G2_35_only_refuses_a_name_that_two_sessions_carry(self):
        a = self.start_lane("alpha", "Alpha Lane")
        c = self.start_lane("alpha-copy", "Alpha Lane")      # a copy started by hand, under the same name
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--only", "Alpha Lane")
        self.assertEqual(rc, 1, out)
        self.assertIn("2 sessions on the record match", out)
        self.assertIn(a, out)
        self.assertIn(c, out)
        time.sleep(3)
        self.assertEqual(len(self.launches()), n, "it started something")
        rc, out = self.tg("revive", "--only", c)              # the id says which one
        self.assertEqual(rc, 0, out)
        self.wait_launches(n + 1)
        time.sleep(2)
        self.assertEqual([x["sid"] for x in self.launches()[n:]], [c])

    def test_G2_56_a_start_line_that_is_printed_shows_a_value_before_the_program_as_set_and_the_start_keeps_it(self):
        """T106. `revive --dry-run` prints each session's start line, and the skill tells an agent to run it and
        show it. A `claude_command` that begins with `VAR=value` (a key, a token) put the value on the screen and
        into whatever read it. Now such a word shows as `VAR=(set)`, the dry run says once that values are not
        shown, and the start itself runs with the real value: only the person's settings file and the sessions'
        start files hold it."""
        secret = "tok-MADE-UP-" + uuid.uuid4().hex
        note = ("(set) stands for a value that is not shown here. It is in your config.json and in the session's "
                "start file; put it back in before you run such a line by hand.")
        a = self.start_lane("alpha", "Alpha Lane")
        b = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.reboot()
        rc, dry = self.tg("revive", "--dry-run")
        self.assertRegex(dry, rf"tmux default:alpha   claude .*--resume {a}\n")
        self.assertNotIn("(set)", dry, "nothing is hidden, so nothing is said")
        for cmd, shown in ((f"TOKEN={secret} claude", "TOKEN=(set) claude"),
                           (f"TOKEN='{secret} two' OTHER=\"{secret}\" claude", "TOKEN=(set) OTHER=(set) claude"),
                           (f"env TOKEN={secret} claude", "env TOKEN=(set) claude"),
                           # wherever the word stands in the person's own command
                           (f"env -i TOKEN={secret} claude", "env -i TOKEN=(set) claude"),
                           (f"env -u X TOKEN={secret} claude", "env -u X TOKEN=(set) claude"),
                           (f"export TOKEN={secret}; claude", "export TOKEN=(set); claude"),
                           (f"export A=1 TOKEN={secret} && claude", "export A=(set) TOKEN=(set) && claude"),
                           (f"sh -c 'TOKEN={secret} exec claude \"$@\"' sh", "sh -c 'TOKEN=(set) exec claude \"$@\"' sh")):
            self.cfg({"claude_command": cmd})
            for args in (("--dry-run",), ("--dry-run", "--all"), ("--dry-run", "--only", "Alpha Lane")):
                rc, dry = self.tg("revive", *args)
                self.assertNotIn(secret, dry, (cmd, args))
                self.assertRegex(dry, rf"tmux default:alpha   {re.escape(shown)} .*--resume {a}\n", (cmd, args))
                self.assertEqual(dry.count(note), 1, (cmd, args, dry))
            # with --start-file the start file is printed whole, as its name says: the note then says that this
            # screen holds the value, never that it is not shown here
            rc, dry = self.tg("revive", "--dry-run", "--start-file")
            self.assertRegex(dry, rf"tmux default:alpha   {re.escape(shown)} .*--resume {a}\n", cmd)
            self.assertIn(secret, dry, cmd)
            self.assertEqual((dry.count(note), dry.count(
                "(set) stands for a value that the start line above does not show. The start file printed under "
                "each line shows it: what is on this screen now holds the value.")), (0, 1), (cmd, dry))
        # the words the tool puts behind the command are left as they are, and a value given any other way (a
        # flag's own word) is shown as it is: the README says so
        other = "made-up-" + uuid.uuid4().hex
        self.cfg({"claude_command": f"claude-wrap --login {other} --as={other}"})
        rc, dry = self.tg("revive", "--dry-run")
        self.assertRegex(dry, rf"tmux default:alpha   claude-wrap --login {other} --as={other} .*--resume {a}\n")
        self.assertNotIn("(set)", dry)
        self.assertIn("A value written any other way, such as a flag's (`--token VALUE`, `--token=VALUE`), is shown "
                      "as it is.", " ".join(fread(os.path.join(ROOT, "README.md")).split()))
        self.cfg({"claude_command": f"TOKEN={secret} claude"})
        n = len(self.launches())
        rc, out = self.tg("revive")                         # the start itself keeps the value
        new = self.wait_launches(n + 2)[n:]
        self.assertEqual(sorted(x["argv"][x["argv"].index("--resume") + 1] for x in new), sorted([a, b]), out)
        self.assertIn("revive: 2 started, 0 already running, 0 failed, 0 need you\n", out)
        for x in new:                                       # each runs with the real value in its environment
            with open(f"/proc/{x['pid']}/environ", "rb") as f:
                self.assertIn(f"TOKEN={secret}".encode(), f.read().split(b"\x00"))
        self.tg("record")
        said = out + "".join(self.tg(*x)[1] for x in (("census",), ("census", "--json"), ("report",),
                                                       ("revive", "--dry-run", "--all")))
        self.assertNotIn(secret, said)
        holds = []
        for d, dirs, files in os.walk(self.home):
            for name in files:
                f = os.path.join(d, name)
                if os.path.islink(f) or not os.path.isfile(f):
                    continue
                with open(f, "rb") as fh:
                    if secret.encode() in fh.read():
                        holds.append(os.path.relpath(f, self.home))
        self.assertEqual(sorted(holds), sorted(
            [os.path.join(".config", "windstorm-tardigrade", "config.json")]
            + [os.path.relpath(self.state(os.path.join("launch", x + ".sh")), self.home) for x in (a, b)]),
            "only the person's settings file and the sessions' start files hold the value")
        # (c) what the README and the skill text say is kept of a key, read here and each part held to the code
        kept = ("It never records a token or a key it finds in a session's environment: for a login token it keeps "
                "the variable's name and an 8-digit one-way fingerprint. What %s into `claude_command`, "
                "`account_command` or `start_command` %s is copied as written into each session's start file (in "
                "Tardigrade's state folder, readable by %s only); a key written there is in those files until the "
                "first revive after the session has left the record, and uninstall keeps that folder.")
        texts = [" ".join(fread(os.path.join(ROOT, *x)).split()) for x in (("README.md",),
                                                                           ("skills", "tardigrade", "SKILL.md"))]
        self.assertIn(kept % ("you write", "yourself", "you"), texts[0])
        self.assertIn(kept % ("the person writes", "themselves", "them"), texts[1])
        self.assertNotIn("never stores a token", " ".join(texts))
        files = {x: self.state(os.path.join("launch", x + ".sh")) for x in (a, b)}
        mode = lambda f: os.stat(f).st_mode & 0o7777
        self.assertEqual([mode(f) for f in files.values()] + [mode(os.path.dirname(files[a])), mode(self.state(""))],
                         [0o600, 0o600, 0o700, 0o700], "readable by the person only")
        pid_a = next(x["pid"] for x in new if a in x["argv"])
        self.kill(pid_a)                                    # alpha ends by itself (its pane keeps the shell it left)
        self.until(lambda: not running(pid_a), "alpha's Claude to end")
        self.tg("record")
        self.assertEqual([r["session_id"] for r in self.table()["lanes"]], [b])
        self.assertIn(secret, fread(files[a]), "off the record, and its start file is there until a revive")
        rc, out = self.tg("revive")
        self.assertIn("revive: 0 started, 1 already running, 0 failed, 0 need you\n", out)
        self.assertEqual((os.path.lexists(files[a]), secret in fread(files[b])), (False, True),
                         "the first revive after it left the record takes its start file away, and no other")
        self.systemd_off()
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("Tardigrade is off this computer", out)
        self.assertIn(secret, fread(files[b]), "uninstall keeps the state folder, start files and all")

    def test_G2_55_only_finds_a_session_on_the_down_list_and_never_says_it_is_not_on_the_record(self):
        """T104. Seen on a stock machine with 0.1.10.1: the census listed a session under "DOWN: on the record", and
        `revive --only` for it answered "no session on the record is named ...". Now it is found there. The step
        the census prints under the table is taken as printed."""
        a = self.start_lane("alpha", "Alpha Lane")
        b = self.start_lane("beta", "Beta Lane")
        self.start_lane("gamma", "Gamma Lane")
        self.tg("record", "--force")
        names = lambda key: sorted(r["name"] for r in self.table()[key])
        by_sid = {x["sid"]: x["pid"] for x in self.launches()}
        self.kill(by_sid[a])                                # it ends by itself
        self.until(lambda: self.tmux(self.sock, "has-session", "-t", "=alpha")[0] != 0, "its window to go")
        rc, out = self.tg("record")
        self.assertEqual((names("lanes"), names("down")), (["Beta Lane", "Gamma Lane"], ["Alpha Lane"]), out)
        census = self.tg("census")[1]
        self.assertIn("1 of the sessions under DOWN are on the down list: a plain `tardigrade revive` does not start "
                      "them. To bring one back: `tardigrade revive --only NAME` (its id where two carry one name).", census)
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.assertIn("revive: 0 started, 2 already running, 0 failed, 0 need you\n", out)
        rc, dry = self.tg("revive", "--only", "Alpha Lane", "--dry-run")
        self.assertIn("revive --only: Alpha Lane was on the down list; a dry run leaves it there", dry)
        self.assertRegex(dry, rf"tmux default:alpha   claude .*--resume {a}\n")
        self.assertEqual((names("down"), len(self.launches())), (["Alpha Lane"], n))
        rc, out = self.tg("revive", "--only", "Alpha Lane")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("no session on the record", out)
        self.assertIn("revive --only: Alpha Lane was on the down list; it is on the list a revive reads again", out)
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"][new["argv"].index("--resume") + 1], a, out)
        rc, out = self.tg("record")
        self.assertEqual((names("lanes"), names("down")), (["Alpha Lane", "Beta Lane", "Gamma Lane"], []), out)
        self.assertEqual([k for r in self.table()["lanes"] for k in r if k.startswith("not_started")], [])
        census = self.tg("census")[1]
        self.assertIn("Running: 3 ", census)
        self.assertNotIn("on the down list", census)
        # a session its owner ended: by name, one line says so with the step; the step (its id) brings it back
        self.kill(by_sid[b])
        self.until(lambda: self.tmux(self.sock, "has-session", "-t", "=beta")[0] != 0, "its window to go")
        self.conversation(b, time.time() + 2)           # after the last record saw it (the stamp has whole seconds)
        rc, out = self.tg("record")
        self.assertEqual([(r["name"], r.get("ended")) for r in self.table()["down"]], [("Beta Lane", "exit")], out)
        n = len(self.launches())
        rc, out = self.tg("revive", "--only", "Beta Lane")
        self.assertEqual(rc, 1, out)
        self.assertIn("revive --only: Beta Lane is on the down list: its owner ended it with /exit at ", out)
        self.assertIn(f". To bring it back anyway: revive --only {b}", out)
        self.assertEqual(len(self.launches()), n)
        rc, out = self.tg("revive", "--only", b)
        self.assertEqual(rc, 0, out)
        self.assertIn("revive --only: Beta Lane was on the down list (ended by its owner); it is on the list a revive "
                      "reads again", out)
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"][new["argv"].index("--resume") + 1], b, out)
        # a name that is on neither list is still said as that
        rc, out = self.tg("revive", "--only", "Nobody Lane")
        self.assertEqual((rc, "revive --only: no session on the record is named 'Nobody Lane'" in out), (1, True), out)

    def test_G3_37_under_down_last_seen_is_when_a_record_last_saw_the_session_running(self):
        """T105. Seen on a stock machine with 0.1.10.1: "last seen 2 s ago" for a session that had not run for five
        minutes; the number was counted from the moment the record noted it gone. A row whose two times differ by
        ten minutes says the time it was last seen running."""
        a = self.start_lane("alpha", "Alpha Lane")
        self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.kill(self.launches()[0]["pid"])
        self.until(lambda: self.tmux(self.sock, "has-session", "-t", "=alpha")[0] != 0, "its window to go")
        t = self.table()
        for r in t["lanes"]:
            if r["session_id"] == a:
                r["last_seen"] -= 600                       # the last record that saw it running: ten minutes ago
        jdump(t, os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json"))
        rc, out = self.tg("record")
        row = self.table()["down"][0]
        self.assertEqual((row["name"], 600 <= row["down_since"] - row["last_seen"] < 660), ("Alpha Lane", True), out)
        line = [x for x in self.tg("census")[1].splitlines() if x.startswith("  Alpha Lane")]
        self.assertEqual(len(line), 1, line)
        self.assertIn("last seen 10 min ago", line[0])

    def test_G2_36_an_exit_that_only_the_prompt_history_has_still_counts(self):
        # seen on real machines with Claude Code 2.1.330: /exit was typed, Claude ended, and its conversation has no
        # /exit line at all. The prompt history has it. Alpha's last prompt is /exit, after the record saw it:
        # ended. Beta typed /exit and then more (a resume): not ended. Gamma's /exit is older than the record.
        a, b, c = (self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta", "gamma"))
        self.tg("record", "--force")
        seen = self.table()["lanes"][0]["last_seen"]
        private = "PRIVATE-WORDS-7f3 my bank pin"           # what a person typed: it must reach nothing of ours
        other = str(uuid.uuid4())                           # a session not on the record
        hist = [(a, "fix the tests " + private, seen - 50), (a, "/exit", seen + 2), (b, "/exit", seen + 1),
                (b, "go on " + private, seen + 3), (c, "/exit", seen - 100), (a, "/exit now", seen - 300),
                (other, "/exit", seen + 5), (other, private, seen + 6)]
        hp = os.path.join(self.home, ".claude", "history.jsonl")
        with open(hp, "w") as f:
            for sid, words, t in hist:
                f.write(json.dumps({"display": words, "pastedContents": {"1": private}, "timestamp": int(t * 1000),
                                    "project": self.work, "sessionId": sid}) + "\n")
            f.write("not json " + private + "\n" + json.dumps({"display": "/exit", "timestamp": int((seen + 9) * 1000)}) + "\n")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.wait_launches(n + 2)
        time.sleep(2)
        self.assertEqual(sorted(x["sid"] for x in self.launches()[n:]), sorted([b, c]), out)
        self.assertIn("skip  Alpha Lane: its owner ended it with /exit", out)
        outs = [out]
        for cmd in (("census",), ("record",), ("report",), ("census", "--json")):
            outs.append(self.tg(*cmd)[1])
        self.assertIn("ended by its owner (/exit)", outs[1])
        st = os.path.join(self.home, ".local", "state", "windstorm-tardigrade")
        files = [os.path.join(dp, f) for d in (st, os.path.join(self.home, ".config", "windstorm-tardigrade"))
                 for dp, _, fs in os.walk(d) for f in fs]
        for text in outs + [fread(f) for f in files]:
            self.assertNotIn("PRIVATE-WORDS", text)
            self.assertNotIn("bank pin", text)
        # a history that cannot be used: nobody is taken as ended by it, and the log says the check was not made
        with open(hp, "w") as f:
            f.write("{this is not what Claude Code writes}\n")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.wait_launches(n + 2)
        self.assertIn("could not be used: it does not look as expected", out)
        import importlib.util                           # and nobody is taken as ended by it: one too many is the safe side
        tg = self.load_tool()
        tg.HOME, tg.STATE = self.home, st
        tg.LOG = os.path.join(st, "tardigrade.log")
        self.assertEqual(tg.exited_at({"session_id": a, "last_seen": seen}), 0)
        os.remove(hp)
        self.reboot()
        rc, out = self.tg("revive")
        self.assertIn("is not there (no history.jsonl in that folder)", out)       # G2.54: not there is not "unreadable"

    def test_G2_54_a_prompt_history_that_is_not_there_is_never_said_as_one_that_could_not_be_read(self):
        # T89: seen on a stock VM, in a new account with no ~/.claude folder: the boot run's log said the history
        # "cannot be read (FileNotFoundError)". Three answers, three wordings, and the same decision under each word.
        a, b, c = (self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta", "gamma"))
        hp = os.path.join(self.home, ".claude", "history.jsonl")
        tail = "A session ended with /exit is known only from its conversation or its pane"

        def round_(expect):
            """a record, a reboot and a revive: (what the revive printed, the ids it started)"""
            self.tg("record", "--force")
            seen = max(r["last_seen"] for r in self.table()["lanes"])
            yield seen
            self.reboot()
            n = len(self.launches())
            rc, out = self.tg("revive")
            self.wait_launches(n + expect)
            time.sleep(2)
            yield out, sorted(x["sid"] for x in self.launches()[n:])

        # 1. not there: said as not there; nobody is taken as ended by it, and the conversation's last turn still counts
        self.assertFalse(os.path.lexists(hp))
        r = round_(2)
        self.conversation(c, next(r) + 2)                   # Gamma's owner typed /exit: its conversation says so
        out, started = next(r)
        self.assertEqual(started, sorted([a, b]), out)
        self.assertIn(f"the prompt history in {os.path.dirname(hp)} is not there (no history.jsonl in that folder). {tail}", out)
        self.assertNotIn("could not be read", out)
        self.assertNotIn("cannot be read", out)
        self.assertIn("skip  Gamma Lane: its owner ended it with /exit", out)
        # 2. there, and it cannot be opened as a file: said as could not be read, with the cause; the same decision
        os.mkdir(hp)
        r = round_(2)
        next(r)
        out, started = next(r)
        self.assertEqual(started, sorted([a, b]), out)
        self.assertIn(f"the prompt history in {os.path.dirname(hp)} could not be read (IsADirectoryError: Is a directory). {tail}", out)
        self.assertNotIn("is not there", out)
        os.rmdir(hp)
        if os.geteuid() != 0:                               # a permission error is the same answer (root reads anything)
            with open(hp, "w") as f:
                f.write(json.dumps({"display": "/exit", "timestamp": int((time.time() + 60) * 1000), "sessionId": a}) + "\n")
            os.chmod(hp, 0)
            r = round_(2)
            next(r)
            out, started = next(r)
            os.chmod(hp, 0o600)
            self.assertEqual(started, sorted([a, b]), out)
            self.assertIn("could not be read (PermissionError: Permission denied)", out)
            self.assertNotIn("is not there", out)
            os.remove(hp)
        # 3. there and readable: no word about it, and Alpha, whose last typed prompt is /exit, stays ended
        r = round_(1)
        seen = next(r)
        with open(hp, "w") as f:
            f.write(json.dumps({"display": "/exit", "timestamp": int((seen + 2) * 1000), "sessionId": a}) + "\n")
        out, started = next(r)
        self.assertEqual(started, [b], out)
        self.assertIn("skip  Alpha Lane: its owner ended it with /exit", out)
        self.assertNotIn("the prompt history in", out)

    def conversation(self, sid, last_command_at):
        """a conversation whose last entry is /exit, then what Claude Code writes after it"""
        d = os.path.join(self.home, ".claude", "projects", "-work")
        os.makedirs(d, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(last_command_at)) + ".000Z"
        with open(os.path.join(d, sid + ".jsonl"), "w") as f:
            for e in ({"type": "assistant", "timestamp": stamp, "message": {"content": [{"type": "text", "text": "done"}]}},
                      {"type": "user", "isMeta": True, "timestamp": stamp, "message": {"content": "<local-command-caveat>x"}},
                      {"type": "user", "timestamp": stamp, "message": {"content":
                          "<command-name>/exit</command-name>\n<command-message>exit</command-message>"}},
                      {"type": "user", "timestamp": stamp, "message": {"content": "<local-command-stdout>Bye!</local-command-stdout>"}},
                      {"type": "cost-state"}):
                f.write(json.dumps(e) + "\n")

    def test_G2_36_a_session_its_owner_ended_is_not_brought_back_and_a_crash_is_still_a_crash(self):
        ids = [self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta", "gamma", "delta", "echo")]
        a, b, c, d, e = ids
        self.tg("record", "--force")
        seen = self.table()["lanes"][0]["last_seen"]
        self.conversation(a, seen + 2)           # alpha's owner typed /exit after the record saw it running
        self.conversation(e, seen - 100)         # echo's /exit is older: it was resumed after it (moved, say)
        pids = {x["sid"]: x["pid"] for x in self.launches()}
        for sid in (a, b, c, d):                 # four gone at once: alpha by its owner, three by a crash
            self.kill(pids[sid])
        time.sleep(2)
        self.tg("record")
        t = self.table()
        self.assertEqual(sorted(r["session_id"] for r in t["lanes"] if r.get("lost_since")), sorted([b, c, d]),
                         "the three that went without /exit are still a crash")
        self.assertEqual([(r["session_id"], r.get("ended")) for r in t["down"]], [(a, "exit")])
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive")
        new = self.wait_launches(n + 4)
        time.sleep(3)
        self.assertEqual(sorted(x["sid"] for x in self.launches()[n:]), sorted([b, c, d, e]))
        rc, out = self.tg("census")
        self.assertIn("ended by its owner (/exit)", out)
        # still on the record (not yet written down as down), a name is not enough to bring it back; its id is
        self.kill([x for x in self.launches()[n:] if x["sid"] == e][0]["pid"])
        time.sleep(1.5)
        rc, out = self.tg("revive")                # its pane holds the shell it left
        # G5.19: the revive above made that tmux session for it, and the mark stays on: it is started again there
        self.assertIn("revive: 1 started, 3 already running, 0 failed, 0 need you\n", out)
        again = self.wait_launches(n + 5)[-1]
        self.assertEqual(again["sid"], e)
        self.kill(again["pid"])
        time.sleep(1.5)
        self.tmux(self.sock, "kill-session", "-t", "=echo")     # and then its owner closed that too
        self.conversation(e, time.time() + 1)
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.assertIn("skip  Echo Lane: its owner ended it with /exit", out)
        rc, out = self.tg("revive", "--only", "Echo Lane")
        self.assertEqual(rc, 1, out)
        self.assertIn(f"revive --only {e}", out)
        time.sleep(2)
        self.assertEqual(len(self.launches()), n)
        rc, out = self.tg("revive", "--only", e)
        self.assertEqual(rc, 0, out)
        self.wait_launches(n + 1)

    def print_conversation(self, sid, *entrypoints):
        """a conversation whose lines name these entrypoints in turn, as Claude Code writes them"""
        d = os.path.join(self.home, ".claude", "projects", "-work")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, sid + ".jsonl"), "w") as f:
            for ep in entrypoints:
                for kind, text in (("user", "Run the shell command sleep 200"), ("assistant", "ok")):
                    f.write(json.dumps({"type": kind, "entrypoint": ep, "sessionId": sid,
                                        "message": {"role": kind, "content": text}}) + "\n")

    def test_G2_47_a_row_whose_conversation_is_a_print_mode_run_is_never_started(self):
        a = self.start_lane("alpha", "Alpha Lane")
        b = self.start_lane("beta", "Beta Lane")
        c = self.start_lane("gamma", "Gamma Lane")
        self.tg("record", "--force")                    # the record 0.1.8 could write: beta and gamma were claude -p runs
        self.reboot()
        self.print_conversation(b, "sdk-cli")
        self.print_conversation(c, "sdk-cli", "cli")    # gamma's was resumed by hand in a terminal later: a session again
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.wait_launches(n + 2)
        time.sleep(2)
        self.assertEqual(sorted(x["sid"] for x in self.launches()[n:]), sorted([a, c]), out)
        self.assertIn("skip  Beta Lane: its conversation is a print-mode or SDK run (entrypoint sdk-cli)", out)
        # seen on a real machine: it is not "already running"; it is never started at all
        self.assertIn("revive: 2 started, 0 already running, 0 failed, 0 need you, 1 never started (print-mode runs, "
                      "folders left alone)", out)
        src = fread(SCRIPT)                     # and a print-mode run still running is a helper, not a DOWN row
        cd = src[src.index("def census_data():"):src.index("coming = [r for r in down", src.index("def census_data():"))]
        self.assertIn('and r["session_id"] not in held_now', cd)
        rc, out = self.tg("revive", "--only", b)
        self.assertEqual(rc, 1, out)
        rc, out = self.tg("census")
        self.assertIn("a print-mode run (claude -p), never brought back", out)
        # with nothing running, the record keeps the record before it, but not the print-mode run
        self.reboot()
        rc, out = self.tg("record")
        self.assertIn("print-mode run(s) moved to the down list: Beta Lane", out)
        t = self.table()
        self.assertEqual(sorted(r["session_id"] for r in t["lanes"]), sorted([a, c]))
        self.assertEqual([(r["session_id"], r.get("ended")) for r in t["down"]], [(b, "print-run")])
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.wait_launches(n + 2)
        time.sleep(2)
        self.assertEqual(sorted(x["sid"] for x in self.launches()[n:]), sorted([a, c]), out)

    def test_G2_12_with_trust_on_the_question_is_answered_for_the_sessions_own_folder(self):
        rc, out = self.tg("trust", "on")
        self.assertIn("Trust answering is ON", out)
        self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        self.reboot()
        with open(os.path.join(self.home, "trustprompt"), "w") as f:
            f.write("1")
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        self.assertIn('answered Yes to "trust this folder?" for its own folder ' + self.work, out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["trust_answered"], rep["not_loaded"], rep["loaded"]), (["Alpha Lane"], [], ["Alpha Lane"]), out)
        rc, cj = self.tg("census", "--json")
        d = json.loads(cj)
        self.assertEqual((d["running"], d["trust"], d["sessions"]["hidden"][0]["status"]), (1, 0, "idle"), cj)

    def test_G2_13_enter_is_sent_only_with_the_cursor_on_yes(self):
        src = fread(SCRIPT)
        body = src[src.index("def answer_trust(r):"):src.index("QUESTION = re.compile")]
        self.assertEqual(body.count("send-keys"), 2)                # Down and Enter
        enter = body.index('"Enter"')
        check = body.rindex("if not foot or not YES_LINE.search(foot):", 0, enter)
        self.assertLess(body.rindex("foot = look()", 0, check), check)      # the screen is read again,
        self.assertLess(check, enter)                                       # and checked, just before Enter
        self.assertIn('if CFG["answer_trust"] and answer_trust(r):', src)   # reached only through the opt-in
        self.assertEqual(src.count("answer_trust(r)"), 2)           # its definition and one call, in the watch

    def test_G2_25_no_key_is_sent_when_the_question_is_only_quoted_and_another_dialog_is_open(self):
        self.tg("trust", "on")
        self.recorded_then_rebooted()
        with open(os.path.join(self.home, "usagebox"), "w") as f:
            f.write("1")                        # quoted trust text above a dialog that offers usage credits
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        self.assertFalse(os.path.exists(os.path.join(self.home, "keys.log")), "a key was sent into the wrong dialog")
        self.assertNotIn("answered Yes", out)
        self.assertIn("count: started=1 LOADED=0 UP-NOT-LOADED=1 DOWN=0", out)
        rc, cj = self.tg("census", "--json")
        self.assertEqual(json.loads(cj)["trust"], 0)                 # and the census does not call it trust? either
        self.assertFalse(os.path.exists(os.path.join(self.home, "keys.log")))

    def test_G2_26_the_tmux_server_is_started_the_machines_own_way(self):
        conf = os.path.join(self.home, "tmux-own.conf")
        with open(conf, "w") as f:
            f.write("set -g history-limit 4242\nset -g status off\n")
        self.cfg({"tmux_conf": conf})
        self.recorded_then_rebooted()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        self.assertEqual(self.tmux(self.sock, "show", "-gv", "history-limit")[1].strip(), "4242")
        self.assertEqual(self.tmux(self.sock, "show", "-gv", "status")[1].strip(), "off")
        self.reboot()
        # a machine's own start command: it starts the server with a keeper session, from the env_file's environment
        envf = os.path.join(self.home, "desktop.env")
        with open(envf, "w") as f:
            f.write("DISPLAY=:10.0\n")
        self.script("startsrv", f'#!/bin/bash\necho "$DISPLAY" > "$HOME/startsrv.log"\n'
                                f'exec {TMUX} -S "$1" -f "{conf}" new-session -d -s _keeper "sleep 600"\n')
        self.cfg({"tmux_conf": "", "env_file": envf, "server_command": "startsrv {socket}", "server_socket": "default"})
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.wait_launches(n + 1)
        self.assertIn("your server_command ran (exit 0); the server is up", out)
        self.assertEqual(fread(os.path.join(self.home, "startsrv.log")).strip(), ":10.0")
        self.assertEqual(sorted(self.tmux(self.sock, "list-sessions", "-F", "#{session_name}")[1].split()), ["_keeper", "alpha"])
        self.assertEqual(self.tmux(self.sock, "show", "-gv", "history-limit")[1].strip(), "4242")
        self.assertEqual(self.tmux(self.sock, "show-environment", "-g", "DISPLAY")[1].strip(), "DISPLAY=:10.0")

    def test_G2_27_an_env_file_never_moves_a_session_out_of_its_folder_or_sets_the_shells_own_variables(self):
        envf = os.path.join(self.home, "desktop.env")
        with open(envf, "wb") as f:
            f.write(b"PWD=/usr\0OLDPWD=/\0UID=0\0EUID=0\0SHLVL=7\0TERM=dumb\0TMUX=/nowhere,1,0\0DISPLAY=:10.0\0")
        self.cfg({"env_file": envf})
        with open(os.path.join(self.home, ".bashrc"), "w") as f:
            f.write("cd /tmp\n")               # and a start file of the shell that changes folder
        sid = self.recorded_then_rebooted()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual((new["cwd"], new["seen"]["PWD"], new["seen"]["DISPLAY"]), (self.work, self.work, ":10.0"))
        text = fread(self.state(os.path.join("launch", sid + ".sh")))
        for name in ("PWD", "OLDPWD", "UID", "EUID", "SHLVL", "TERM", "TMUX"):
            self.assertNotIn(f"export {name}=", text)
        self.assertEqual(jload(self.state("last-revive.json"))["loaded"], ["Alpha Lane"])

    def test_G2_33_a_display_or_session_bus_that_is_gone_is_not_handed_to_a_session(self):
        import importlib.util
        x11 = os.path.join(self.tmp, "x11")          # made by setUp, with display :10 in it
        rt = os.path.join(self.tmp, "run")
        os.makedirs(rt)
        with open(os.path.join(rt, "bus"), "w") as f:
            f.write("")
        # the test setting counts only with the suite's marker in HOME (G5.15): with the runner's own home this test
        # read the machine's real X11 folder (seen 2026-11-24 on CI, which has no display :10)
        tg = self.load_tool()
        self.assertTrue(tg.TESTING)
        gone = [("DISPLAY", ":11.0"), ("XDG_RUNTIME_DIR", rt), ("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/dbus-gone,guid=1")]
        self.assertEqual(tg.live_desktop(gone), [("XDG_RUNTIME_DIR", rt), ("DBUS_SESSION_BUS_ADDRESS", f"unix:path={rt}/bus")])
        there = [("DISPLAY", ":10.0"), ("DBUS_SESSION_BUS_ADDRESS", f"unix:path={rt}/bus"), ("LANG", "C")]
        self.assertEqual(tg.live_desktop(there), there)             # what is there is left as it is
        nobus = [("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/dbus-gone"), ("XDG_RUNTIME_DIR", "/nowhere")]
        self.assertEqual(tg.live_desktop(nobus), [("XDG_RUNTIME_DIR", "/nowhere")])
        self.assertEqual(tg.live_desktop([("DISPLAY", "otherhost:0")]), [("DISPLAY", "otherhost:0")])   # not a local display

    def test_G2_28_one_process_list_per_pass_is_taken_and_handed_down(self):
        src = fread(SCRIPT)
        body = src[src.index("class Watch:"):src.index("def revive():")]
        self.assertEqual(body.count("procs()"), 2)                  # one per pass, one for the count
        self.assertEqual(body.count("came_up(r, P)"), 2)
        self.assertNotIn("came_up(r)", body)
        loop = src[src.index("    while True:\n        if pending and not tries_done"):src.index("        if not tries_done and (not pending")]
        self.assertEqual(loop.count("procs()"), 1)                  # fresh for each session, then handed down
        self.assertIn("running_sids(P)", loop)

    def test_G2_29_sessions_that_are_up_are_looked_after_while_another_start_is_still_retried(self):
        self.tg("trust", "on")
        self.cfg({"retry_min": 0.25})
        self.start_lane("alpha", "Alpha Lane")
        bad = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.reboot()
        for name, text in (("trustprompt", "1"), ("failsid", bad)):
            with open(os.path.join(self.home, name), "w") as f:
                f.write(text)
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertIn("Beta Lane: GAVE UP after", out)
        self.assertIn("its tmux session holds a shell", out)        # what is in its place now (G4.9)
        self.assertLess(out.index('Alpha Lane: answered Yes to "trust this folder?"'), out.index("Beta Lane: GAVE UP"), out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["failed"], rep["loaded"]), (["Alpha Lane"], ["Beta Lane"], ["Alpha Lane"]), out)

    def test_G2_32_a_question_is_answered_while_later_sessions_are_still_being_started(self):
        self.tg("trust", "on")
        self.cfg({"starts_at_once": 1})         # one at a time, so "later sessions" are still to be started
        for n in ("alpha", "beta", "gamma"):
            self.start_lane(n, n.title() + " Lane")
        self.tg("record", "--force")
        self.reboot()
        with open(os.path.join(self.home, "trustprompt"), "w") as f:
            f.write("1")
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 3)
        self.assertLess(out.index('Alpha Lane: answered Yes to "trust this folder?"'), out.index("Gamma Lane: up"), out)
        self.assertIn("count: started=3 LOADED=3 UP-NOT-LOADED=0 DOWN=0", out)

    def test_G2_30_a_session_nobody_ever_wrote_in_comes_back_new_under_the_same_name(self):
        sid = self.recorded_then_rebooted()
        with open(os.path.join(self.home, "noconv"), "w") as f:
            f.write(sid)
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = self.wait_launches(n + 2)[n:]
        self.assertEqual(new[0]["argv"][-2:], ["--resume", sid])                 # tried as itself first
        self.assertEqual(new[1]["argv"], ["--dangerously-skip-permissions", "--model", "claude-opus-5-5", "--effort", "high",
                                          "--name", "Alpha Lane"])
        self.assertEqual(new[1]["cwd"], self.work)
        self.assertIn("Starting it new, with the same name, folder and flags", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["fresh"], rep["failed"], rep["loaded"]), (["Alpha Lane"], ["Alpha Lane"], [], ["Alpha Lane"]), out)
        self.assertEqual(self.typed(), [])      # no "your conversation is intact" note for a session that had none
        rc, cj = self.tg("census", "--json")
        self.assertEqual([r["name"] for r in json.loads(cj)["sessions"]["hidden"]], ["Alpha Lane"])

    def test_G2_43_a_session_slow_to_say_it_has_no_conversation_is_still_started_new(self):
        # seen on a Mac: the start fetched a login first, so "No conversation found" came after the settle look
        self.cfg({"settle_s": 2})
        sid = self.recorded_then_rebooted()
        with open(os.path.join(self.home, "noconvslow"), "w") as f:
            f.write(sid)
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = self.wait_launches(n + 2)[n:]
        self.assertEqual(new[0]["argv"][-2:], ["--resume", sid])
        self.assertEqual(new[1]["argv"][-2:], ["--name", "Alpha Lane"], out)          # started new, once
        self.assertIn("it said so only after it was counted up", out)
        self.assertEqual(len(self.launches()), n + 2)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["fresh"], rep["failed"], rep["needs_you"]),
                         (["Alpha Lane"], ["Alpha Lane"], [], []), out)
        self.assertEqual(self.typed(), [])

    def test_G2_44_a_start_that_is_up_a_little_late_is_looked_after_at_once(self):
        # seen on a Mac: the first start after login waited on the network for its login and missed the 8 s look
        self.cfg({"settle_s": 1, "retry_gap_s": 60, "claude_command": "sleep 3; claude"})
        sid_a = self.recorded_then_rebooted()
        n = len(self.launches())
        t0 = time.time()
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertIn("Alpha Lane: not running yet (attempt 1); will retry", out)
        self.assertIn("Alpha Lane: up (came up late)", out)
        self.assertLess(time.time() - t0, 45, "it waited for the retry round, 60 s away")
        self.assertEqual(len(self.launches()), n + 1)               # one start, never a second copy
        self.assertEqual(len(self.wait_typed(1)), 1)                # and it is looked after: its note is typed
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["failed"]), (["Alpha Lane"], []), out)
        # given up, then up after all, while the watch still runs for another session: counted once, as back
        b = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.reboot()
        self.script("lateclaude", '#!/bin/bash\ncase " $* " in *" %s "*) sleep 5;; esac\nexec claude "$@"\n' % sid_a)
        self.cfg({"settle_s": 1, "retry_min": 0.03, "retry_gap_s": 60, "claude_command": "lateclaude",
                  "wake": "note", "watch_min": 0.25})
        with open(os.path.join(self.home, "status"), "w") as f:
            f.write("waiting")                   # Beta is up but not idle: the watch goes on
        rc, out = self.tg("revive")
        self.assertIn("Alpha Lane: GAVE UP after 1 attempt(s)", out)
        self.assertIn("Alpha Lane: up (came up after it was given up)", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((sorted(rep["started"]), rep["failed"]), (["Alpha Lane", "Beta Lane"], []), out)

    def test_G2_14_the_permission_mode_comes_back_word_for_word_never_looser(self):
        a = self.start_lane("alpha", "Alpha Lane", flags="--permission-mode acceptEdits")
        b = self.start_lane("beta", "Beta Lane", flags="--permission-mode bypassPermissions")
        c = self.start_lane("gamma", "Gamma Lane", flags="--model claude-opus-5-5")     # no mode: the default one
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = {x["sid"]: x["argv"] for x in self.wait_launches(n + 3)[n:]}
        self.assertEqual(new[a][new[a].index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(new[b][new[b].index("--permission-mode") + 1], "bypassPermissions")
        self.assertNotIn("--permission-mode", new[c])
        for argv in new.values():
            self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_G2_15_a_session_pinned_to_an_account_starts_through_its_wrapper(self):
        self.script("acctwrap", '#!/bin/bash\necho "$@" >> "$HOME/acctwrap.log"\n[ "$1" = use ] || exit 2\n'
                                'export ACCT_NAME="$2"; shift 2\nexec claude "$@"\n')
        self.cfg({"account_env": "ACCT_NAME", "account_command": "acctwrap use {account}"})
        a = self.start_lane("alpha", "Alpha Lane", extra_env={"ACCT_NAME": "sam"})
        b = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.assertEqual({r["name"]: r["account"] for r in self.table()["lanes"]}, {"Alpha Lane": "sam", "Beta Lane": ""})
        self.reboot()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = {x["sid"]: x for x in self.wait_launches(n + 2)[n:]}
        wrapped = fread(os.path.join(self.home, "acctwrap.log")).splitlines()
        self.assertEqual(len(wrapped), 1)                           # only the pinned session went through it
        self.assertTrue(wrapped[0].startswith("use sam ") and wrapped[0].endswith("--resume " + a), wrapped)
        self.assertEqual(new[a]["seen"].get("ACCT_NAME"), "sam")
        self.assertNotIn("ACCT_NAME", new[b]["seen"])

    def test_G2_41_the_account_label_is_set_by_the_start_file_not_left_to_the_wrapper(self):
        # a real wrapper (one that fetches a login token) does not put the label back; the G2.15 stand-in did
        self.script("acctwrap", '#!/bin/bash\necho "$@" >> "$HOME/acctwrap.log"\n[ "$1" = use ] || exit 2\n'
                                'shift 2\nexec claude "$@"\n')
        self.cfg({"account_env": "ACCT_NAME", "account_command": "acctwrap use {account}"})
        a = self.start_lane("alpha", "Alpha Lane", extra_env={"ACCT_NAME": "sam"})
        b = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = {x["sid"]: x for x in self.wait_launches(n + 2)[n:]}
        self.assertEqual(new[a]["seen"].get("ACCT_NAME"), "sam")       # so the next record still knows it is pinned
        self.assertNotIn("ACCT_NAME", new[b]["seen"])
        self.assertIn("export ACCT_NAME=sam\n", fread(self.state(os.path.join("launch", a + ".sh"))))
        self.assertIn("unset ACCT_NAME\n", fread(self.state(os.path.join("launch", b + ".sh"))))
        self.tg("record", "--force")
        self.assertEqual({r["name"]: r["account"] for r in self.table()["lanes"]}, {"Alpha Lane": "sam", "Beta Lane": ""})

    def test_G2_39_every_start_flag_comes_back_and_none_is_dropped_silently(self):
        flags = ("--name 'Alpha Lane' --plugin-dir /opt/p1 --plugin-dir '/opt/p two' --settings /opt/s.json "
                 "--agents '{\"x\": {}}' --remote-control "
                 "--permission-mode acceptEdits --verbose --allowed-tools Bash Edit "
                 "--mcp-config '{\"headers\": {\"Authorization\": \"Bearer abc\"}}' --worktree wt "
                 "--frobnicate maybe 'do the thing'")
        sid = self.start_lane("alpha", "Alpha Lane", flags=flags)
        rc, out = self.tg("record", "--force")
        self.assertIn("record: Alpha Lane: start flag(s) not carried: --agents (inline JSON: only a file's path is "
                      "carried); --mcp-config (its value looks like it holds a "
                      "secret); --worktree (it would start something other than this session here); --frobnicate "
                      "(not known to this version: is the word after it its value?)", out)
        self.assertIn("record: Alpha Lane: --remote-control is carried: it comes back with Remote Control on", out)
        self.assertNotIn("not carried", self.tg("record", "--force")[1])     # named once, not at every record
        for f in (self.state("tardigrade.log"), os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")):
            self.assertNotIn("Bearer abc", fread(f))
            self.assertNotIn("do the thing", fread(f))                        # a first message is never kept
        self.reboot()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertEqual(self.wait_launches(n + 1)[-1]["argv"],
                         ["--permission-mode", "acceptEdits", "--name", "Alpha Lane", "--plugin-dir", "/opt/p1",
                          "--plugin-dir", "/opt/p two", "--settings", "/opt/s.json", "--remote-control", "--verbose",
                          "--allowed-tools",
                          "Bash", "Edit", "--resume", sid])
        # the name it has now goes with --name: a session renamed since it started keeps its new name
        import importlib.util
        tg = self.load_tool()
        self.assertEqual(tg.flags_of({"mode": "default", "name": "New Name", "flags": ["-n", "Old Name", "--chrome"]}),
                         ["-n", "New Name", "--chrome"])
        self.assertEqual(tg.start_flags(["claude", "-c", "--resume", "x", "--print", "hi", "--ide", "-d", "api"]),
                         (["--ide", "-d", "api"], []))
        # a value with the shape of a key or token is never carried, whatever the words around it
        for v in ("ghp_" + "a1" * 12, "github_pat_" + "B2" * 12, "xoxb-1234-abcd-efgh", "AKIA" + "ABCDEFGH23456789",
                  "AIza" + "x" * 35, "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0", "sk-ant-api03-abc", "hf_" + "q" * 34):
            self.assertEqual(tg.start_flags(["claude", "--append-system-prompt", f"use {v} here"]),
                             ([], ["--append-system-prompt (its value looks like it holds a secret)"]), v)
        self.assertEqual(tg.start_flags(["claude", "--settings={\"model\": \"x\"}", "--mcp-config", "/a.json", "[1]"]),
                         ([], ["--settings (inline JSON: only a file's path is carried)",
                               "--mcp-config (inline JSON: only a file's path is carried)"]))
        self.assertEqual(tg.start_flags(["claude", "--mcp-config", "/a.json", "/b.json", "--settings=/s.json"]),
                         (["--mcp-config", "/a.json", "/b.json", "--settings=/s.json"], []))
        # the README lists what is carried and what is not
        readme = fread(os.path.join(ROOT, "README.md"))
        for word in ("--plugin-dir", "--worktree", "--resume", "first message"):
            self.assertIn(word, readme[readme.index("### Start flags"):])

    def test_G2_42_the_record_the_table_and_the_log_are_this_users_only(self):
        tsv = os.path.join(self.home, "tables", "lanes.tsv")
        self.cfg({"tsv_path": tsv})
        cfgd = os.path.join(self.home, ".config", "windstorm-tardigrade")
        for d in (self.state(""), cfgd):                           # open, as a Mac's home leaves things
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o755)
        self.recorded_then_rebooted()
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        for d in (self.state(""), cfgd):
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o700, d)
        self.tg("record", "--force")                        # the record's copy, lanes.json.prev, holds start flags too
        for f in (os.path.join(cfgd, "lanes.json"), os.path.join(cfgd, "lanes.json.prev"), tsv,
                  self.state("tardigrade.log"), self.state("last-revive.json"), self.state("progress.json")):
            self.assertEqual(os.stat(f).st_mode & 0o777, 0o600, f)
        # and uninstall leaves no compiled copy behind (seen on a real Mac); not run here: it talks to the real
        # systemctl --user of whoever runs the tests
        src = fread(SCRIPT)
        u = src[src.index("def uninstall():"):src.index("def trust_cmd():")]
        self.assertIn('remove_ours(os.path.join(cache, f), "cache", kept, failed, gone)', u)     # G3.28: our cache only

    def test_G2_42_only_the_newest_few_boot_markers_are_kept_and_they_are_private(self):
        # T38: seen on a Linux machine, 14 markers in two days, at mode 664, the old ones never read again
        os.makedirs(self.state(""), exist_ok=True)
        for i in range(8):
            p = self.state(f"revived-old-boot-{i}")
            with open(p, "w") as f:
                f.write("x\n")
            os.chmod(p, 0o664)
            os.utime(p, (time.time() - 86400 + i * 60,) * 2)
        self.recorded_then_rebooted()
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_BOOT_ID="boot-new")
        left = sorted(f for f in os.listdir(self.state("")) if f.startswith("revived-"))
        self.assertEqual(left, sorted(["revived-boot-new"] + [f"revived-old-boot-{i}" for i in (4, 5, 6, 7)]))
        self.assertEqual(os.stat(self.state("revived-boot-new")).st_mode & 0o777, 0o600)

    def test_G2_16_a_start_command_from_the_settings_replaces_the_start_line(self):
        self.script("mystart", '#!/bin/bash\nprintf "%s|" "$@" >> "$HOME/mystart.log"\nexec claude --resume "$1"\n')
        self.cfg({"start_command": "mystart {session_id} {mode} {model} {name} {flags}"})
        sid = self.recorded_then_rebooted()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertEqual(self.wait_launches(n + 1)[-1]["argv"], ["--resume", sid])
        self.assertEqual(fread(os.path.join(self.home, "mystart.log")),
                         f"{sid}|bypass|claude-opus-5-5|Alpha Lane|--dangerously-skip-permissions|--model|"
                         "claude-opus-5-5|--effort|high|")

    def test_G2_17_an_env_file_reaches_the_revived_session_and_a_secret_in_it_does_not(self):
        envf = os.path.join(self.home, "desktop.env")
        with open(envf, "wb") as f:              # NUL-separated, as `env -0` writes it
            f.write(b"DISPLAY=:10.0\0MY_SETTING=two words\0MY_SECRET_TOKEN=sk-do-not-copy\0")
        self.cfg({"env_file": envf})
        sid = self.recorded_then_rebooted()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        seen = self.wait_launches(n + 1)[-1]["seen"]
        self.assertEqual(seen, {"DISPLAY": ":10.0", "MY_SETTING": "two words", "PWD": self.work})
        self.assertIn("left out MY_SECRET_TOKEN", out)
        for p in (self.state(os.path.join("launch", sid + ".sh")), self.state("tardigrade.log")):
            self.assertNotIn("sk-do-not-copy", fread(p))

    def test_G2_18_a_settings_variable_on_the_carry_list_comes_back_with_its_value(self):
        self.cfg({"carry_env": ["CLAUDE_CODE_AUTO_COMPACT_WINDOW", "MY_SECRET_TOKEN"]})
        a = self.start_lane("alpha", "Alpha Lane", extra_env={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000",
                                                              "MY_SECRET_TOKEN": "sk-do-not-keep"})
        b = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        rows = {r["name"]: r["carry_env"] for r in self.table()["lanes"]}
        self.assertEqual(rows, {"Alpha Lane": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000"}, "Beta Lane": {}})
        self.assertNotIn("sk-do-not-keep", fread(os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")))
        self.reboot()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = {x["sid"]: {k: v for k, v in x["seen"].items() if k != "PWD"} for x in self.wait_launches(n + 2)[n:]}
        self.assertEqual((new[a], new[b]), ({"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000"}, {}))

    def test_G2_19_nothing_but_the_start_command_is_on_a_command_line(self):
        sid = self.recorded_then_rebooted()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = self.wait_launches(n + 1)[-1]
        self.assertEqual(new["argv"], ["--dangerously-skip-permissions", "--model", "claude-opus-5-5", "--effort", "high",
                                       "--resume", sid])
        with open(f"/proc/{new['pid']}/stat") as f:
            parent = int(f.read().rsplit(")", 1)[1].split()[1])
        with open(f"/proc/{parent}/cmdline", "rb") as f:
            shell = f.read().replace(b"\0", b" ").decode()
        start_file = self.state(os.path.join("launch", sid + ".sh"))
        self.assertEqual(shell.strip(), f"bash -ic . {start_file}")   # the shell's whole command line: "read this file"
        self.assertEqual(os.stat(start_file).st_mode & 0o777, 0o600)
        self.assertIn("--resume " + sid, fread(start_file))
        self.assertEqual(len(self.wait_typed(1)), 1)                  # and the note still arrives, typed

    def test_G2_20_the_note_is_not_typed_into_a_session_that_is_not_idle(self):
        self.recorded_then_rebooted()
        with open(os.path.join(self.home, "status"), "w") as f:
            f.write("waiting")                  # the session comes back to a question of its own
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        self.assertEqual(self.typed(), [])
        self.assertIn("the wake-up note was not typed", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["noted"], rep["note_not_typed"], rep["loaded"]), ([], ["Alpha Lane"], ["Alpha Lane"]))

    def test_G2_21_dry_run_all_shows_every_start_line_and_starts_nothing(self):
        sid = self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        n = len(self.launches())
        rc, out = self.tg("revive", "--dry-run")
        self.assertIn("skip  Alpha Lane: already running", out)
        rc, out = self.tg("revive", "--dry-run", "--all")
        self.assertIn("start Alpha Lane", out)
        self.assertIn(f"tmux default:alpha   claude --dangerously-skip-permissions --model claude-opus-5-5 "
                      f"--effort high --resume {sid}", out)
        rc, out = self.tg("revive", "--dry-run", "--all", "--start-file")
        self.assertIn(f"cd -- {self.work} 2>/dev/null\nclaude --dangerously-skip-permissions", out)
        time.sleep(3)
        self.assertEqual(len(self.launches()), n)
        self.assertFalse(os.path.exists(self.state("launch")))       # not even a start file is written
        self.assertNotIn("cd -- ", fread(self.state("tardigrade.log")))     # and its text is not in the log

    def test_G2_22_a_start_that_fails_is_tried_again_in_the_same_tmux_session(self):
        sid = self.recorded_then_rebooted()
        with open(os.path.join(self.home, "failonce"), "w") as f:
            f.write("1")
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertEqual([x["sid"] for x in self.wait_launches(n + 2)[n:]], [sid, sid])
        self.assertIn("will retry", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["failed"]), (["Alpha Lane"], []), out)
        self.assertEqual(self.tmux(self.sock, "list-sessions", "-F", "#{session_name}")[1].split(), ["alpha"])

    def test_G2_34_several_starts_at_once_each_decided_alone(self):
        import re
        # the starts are timed here, not the notes (which wait one at a time per login, G2.45)
        self.cfg({"settle_s": 3, "starts_at_once": 3, "load_max": 50, "load_wait_s": 0, "wake": "idle"})
        ids = [self.start_lane(f"lane{i}", f"Lane {i}") for i in range(6)]
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        t0 = time.time()
        rc, out = self.tg("revive", TARDIGRADE_FAKE_LOAD=99)
        took = time.time() - t0
        self.wait_launches(n + 6)
        self.assertEqual(sorted(x["sid"] for x in self.launches()[n:]), sorted(ids))
        # six starts with a 3-second wait each take at least 18 s one after another; the waits overlapped
        self.assertLess(took, 15, out)
        flying = [int(m) for m in re.findall(r"started \((\d+) of at most 3 not yet seen up\)", out)]
        self.assertEqual(len(flying), 6, out)
        self.assertLessEqual(max(flying), 3)
        self.assertEqual(out.count("above load_max 50"), 6, "load_max is read again before each start")
        # two rows with one session id: neither is started
        t = self.table()
        t["lanes"].append(dict(t["lanes"][0], tmux_session="lane0-copy"))
        jdump(t, os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json"))
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.wait_launches(n + 5)
        time.sleep(3)
        self.assertNotIn(t["lanes"][0]["session_id"], [x["sid"] for x in self.launches()[n:]])
        self.assertIn("the record holds 2 rows with this session id; started none of them", out)

    def test_G4_4_a_waiting_screen_holds_each_place_until_its_session_comes(self):
        import re
        # a machine's own server: started by its server_command, with a boot unit's and a Claude's variables in it
        self.cfg({"settle_s": 2, "starts_at_once": 1, "wake": "idle",
                  "server_command": f"INVOCATION_ID=unit-1 JOURNAL_STREAM=9:9 CLAUDE_CODE_CHILD_SESSION=1 {TMUX} -S {{socket}} "
                                    "new-session -d -s keeper 'sleep 600'; date +%s.%N > $HOME/server-at"})
        ids = [self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta", "gamma", "delta", "echo")]
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        env = dict(self.env, TARDIGRADE_FAKE_UPTIME_S="100")
        run = subprocess.Popen([sys.executable, "-I", SCRIPT, "revive", "--boot"], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            end = time.time() + 20
            while time.time() < end and self.tmux(self.sock, "has-session", "-t", "=echo")[0] != 0:
                time.sleep(0.1)
            screen = self.tmux(self.sock, "capture-pane", "-p", "-t", "=echo:")[1]
            for _ in range(30):
                if "of 5 sessions are back" in screen:
                    break
                time.sleep(0.2)
                screen = self.tmux(self.sock, "capture-pane", "-p", "-t", "=echo:")[1]
            self.assertIn("Windstorm Tardigrade is bringing this session back", screen)
            self.assertIn('Session:  "Echo Lane"', screen)
            self.assertIn("of 5 sessions are back", screen)
            for line in screen.splitlines():                       # nothing a waker could take for a prompt
                self.assertNotIn("❯", line)
                self.assertFalse(re.search(r"[$%#>]\s*$", line), line)
            self.assertEqual(self.tmux(self.sock, "show-options", "-qv", "-t", "=echo:", "@tardigrade_waiting")[1].strip(), "1")
            self.assertEqual(self.tmux(self.sock, "display", "-p", "-t", "=echo:", "#{pane_title}")[1].strip(),
                             "Echo Lane (coming back)")
            self.assertNotIn(self.tmux(self.sock, "display", "-p", "-t", "=echo:", "#{pane_current_command}")[1].strip(),
                             ("bash", "sh", "zsh"))
            made = int(self.tmux(self.sock, "display", "-p", "-t", "=alpha:", "#{session_created}")[1])
            self.assertGreaterEqual(made + 1, float(fread(os.path.join(self.home, "server-at"))), "server first")
            # typed text, Enter and Ctrl+C do nothing there
            self.tmux(self.sock, "send-keys", "-t", "=echo:", "-l", "rm -rf hello")
            self.tmux(self.sock, "send-keys", "-t", "=echo:", "Enter")
            self.tmux(self.sock, "send-keys", "-t", "=echo:", "C-c")
            time.sleep(1.2)
            screen = self.tmux(self.sock, "capture-pane", "-p", "-t", "=echo:")[1]
            self.assertIn("Windstorm Tardigrade is bringing this session back", screen)
            self.assertNotIn("rm -rf hello", screen)
            # meanwhile: census says coming back, never running; record writes nothing
            d = json.loads(self.tg("census", "--json")[1])
            self.assertIn("Echo Lane", [r["name"] for r in d["sessions"]["coming_back"]])
            self.assertNotIn("Echo Lane", [r["name"] for r in d["sessions"]["hidden"] + d["sessions"]["with_window"]])
            self.assertIn("a revive is starting sessions", self.tg("record")[1])
            # a person starts delta by hand somewhere else during the wait; another closes gamma's waiting screen
            d_sid = ids[3]
            self.tmux(self.sock, "new-session", "-d", "-s", "byhand", "-c", self.work, f"claude --resume {d_sid}")
            self.tmux(self.sock, "kill-session", "-t", "=gamma")
            out, _ = run.communicate(timeout=180)
        finally:
            if run.poll() is None:
                run.kill()
        new = self.launches()[n:]
        self.assertEqual(sorted(x["sid"] for x in new if x["sid"] != d_sid), sorted(ids[:3] + ids[4:]), out)
        self.assertEqual(len([x for x in new if x["sid"] == d_sid]), 1, "delta was started a second time")
        self.assertIn("Delta Lane: something else started it meanwhile (tmux default:byhand)", out)
        self.assertNotEqual(self.tmux(self.sock, "has-session", "-t", "=delta")[0], 0, "its waiting screen ended")
        self.assertEqual(self.typed(), [], "something typed at a waiting screen reached Claude")
        for x in [x for x in new if x["sid"] != d_sid]:            # its own starts: none of the unit's or a Claude's variables
            self.assertNotIn("INVOCATION_ID", x["seen"])
            self.assertNotIn("JOURNAL_STREAM", x["seen"])
            self.assertFalse(x["child"])
        def waiters():                                             # this test's own, by the HOME it gave them
            found = []
            for pid in [x for x in os.listdir("/proc") if x.isdigit()]:
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmd = f.read()
                    if b"tardigrade.py\0wait" in cmd:
                        with open(f"/proc/{pid}/environ", "rb") as f:
                            if f"HOME={self.home}".encode() in f.read().split(b"\0"):
                                found.append(pid)
                except OSError:
                    pass
            return found
        self.assertEqual(gone_soon(waiters), [], "a waiting screen outlived its turn")
        for s in ("alpha", "beta", "gamma", "echo"):
            self.assertEqual(self.tmux(self.sock, "show-options", "-qv", "-t", f"={s}:", "@tardigrade_waiting")[1].strip(), "")
        rep = jload(self.state("last-revive.json"))
        self.assertEqual(sorted(rep["loaded"]), ["Alpha Lane", "Beta Lane", "Echo Lane", "Gamma Lane"], out)

    def test_G2_37_the_note_per_session_in_its_own_words_spaced_and_only_into_an_empty_box(self):
        import importlib.util
        tmpd = os.path.join(self.tmp, "fresh-tmp")             # a /tmp that the restart emptied
        os.makedirs(tmpd)
        self.cfg({"wake": "note", "wake_skip": ["^Gamma"], "notes_per_min": 1})
        ids = [self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta", "gamma")]
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_TMP=tmpd)
        self.wait_launches(n + 3)
        typed = self.wait_typed(2)
        by = {x["sid"]: x["text"] for x in typed}
        self.assertEqual(sorted(by), sorted(ids[:2]), out)        # Gamma is on wake_skip
        for text in by.values():
            self.assertIn("This restart emptied /tmp", text)
        lines = [ln for ln in out.splitlines() if "wake-up note typed" in ln]
        import datetime
        stamps = [datetime.datetime.strptime(ln[:24], "%Y-%m-%dT%H:%M:%S%z").timestamp() for ln in lines]
        self.assertEqual(len(stamps), 2, out)
        self.assertGreaterEqual(stamps[1] - stamps[0], 5, "one note a minute (6 s in tests): they were not spaced")
        # the rules, one by one
        tg = self.load_tool()
        tg.CFG.update(wake="idle", wake_names=["^Alpha"], wake_skip=[])
        self.assertEqual([tg.wants_note({"name": x}) for x in ("Alpha Lane", "Beta Lane")], [True, False])
        tg.CFG.update(wake_text="Back at {when}. Carry on.")
        self.assertRegex(tg.wake_note(True), r"^Back at \d{4}-\d\d-\d\d \d\d:\d\d .*\. Carry on\.$")
        rule = "─" * 40
        self.assertTrue(tg.box_empty(f"old text\n{rule}\n❯ \n{rule}\n  ⏵⏵ bypass permissions on\n"))
        self.assertFalse(tg.box_empty(f"old text\n{rule}\n❯ half a sentence\n{rule}\n"))    # someone is typing
        self.assertFalse(tg.box_empty("loaded\n"))                                           # no box: not known
        # a session nobody wrote in shows a dim placeholder in its empty box (Claude Code 2.1.330, a real machine)
        self.assertTrue(tg.box_empty(f"{rule}\n\x1b[39m❯\xa0\x1b[2mTry \"how does <filepath> work?\"\x1b[0m\n{rule}\n"))
        self.assertFalse(tg.box_empty(f"{rule}\n\x1b[39m❯\xa0hello \x1b[2mtypo\x1b[0m\n{rule}\n"))

    def tall_status_revive(self, notice):
        sid = self.recorded_then_rebooted()
        with open(os.path.join(self.home, "tallstatus"), "w") as f:
            f.write("16")
        if notice:
            open(os.path.join(self.home, "limitnotice"), "w").close()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        return sid, out

    def test_G2_40_a_tall_status_line_moves_where_the_box_is_looked_for(self):
        # T36: a real Linux machine draws a 13-line status line under the input box. The foot was read as a
        # fixed 15 lines, so the box was never found there and no revived session got its note
        sid, out = self.tall_status_revive(notice=False)
        self.assertEqual([t["sid"] for t in self.wait_typed(1)], [sid], out)
        self.assertEqual(jload(self.state("last-revive.json"))["noted"], ["Alpha Lane"])

    def test_G2_40_a_notice_above_a_tall_status_line_still_holds_the_note(self):
        # T36: with the box found again, a usage-limit notice above that tall status line must still be seen
        sid, out = self.tall_status_revive(notice=True)
        time.sleep(2)
        self.assertEqual(self.typed(), [], out)
        self.assertIn("Alpha Lane: loaded, but a usage or limit notice is on its screen; the wake-up note was not typed", out)

    def test_G2_40_no_note_is_typed_beside_a_usage_or_limit_notice(self):
        # seen on two real machines at their weekly limit: the notice sits under an ordinary, empty input box
        self.recorded_then_rebooted()
        open(os.path.join(self.home, "limitnotice"), "w").close()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        self.assertEqual(self.typed(), [], out)
        self.assertIn("Alpha Lane: loaded, but a usage or limit notice is on its screen; the wake-up note was not typed", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["noted"], rep["note_not_typed"]), ([], ["Alpha Lane"]))
        self.assertNotIn("is at its usage limit", out)    # a screen can be history: it holds this session only (G2.45)
        # the forms it takes, or may take; and ordinary words are not mistaken for one
        import importlib.util
        tg = self.load_tool()
        for text in ("⚠ Usage limit reached · limit resets 12pm", "/usage-credits to continue now", "usage credits",
                     "You've hit your limit · resets 3pm", "Weekly limit reached", "5-hour limit reached",
                     "Opus limit reached", "Approaching usage limit", "/extra-usage to keep going",
                     "You have reached your plan's usage limit.", "1. Stop and wait", "Switch to usage credits"):
            self.assertTrue(tg.OTHER_DIALOG.search(text), text)
        for text in ("❯ ", "  ⏵⏵ bypass permissions on", "● Done. The tests pass.", "Context left until auto-compact: 12%"):
            self.assertFalse(tg.OTHER_DIALOG.search(text), text)
        # live evidence that a login is AT its limit (G2.45); a warning that it is near is not
        for text in ("⚠ Usage limit reached · limit resets 12pm", "You've hit your weekly limit · resets 12pm",
                     "set to continue automatically at 12pm", "You have reached your plan's usage limit."):
            self.assertTrue(tg.AT_LIMIT.search(text), text)
        for text in ("Approaching usage limit", "/usage-credits to continue now", "Context left until auto-compact: 12%"):
            self.assertFalse(tg.AT_LIMIT.search(text), text)
        # and the same rule holds the trust answer (G2.12): one pattern for both
        src = fread(SCRIPT)
        call = src.index("type_note(r, self.note_for")
        self.assertIn("not OTHER_DIALOG.search(near_box(screen, 15))", src[call - 500:call])

    def limit_reply(self, sid, then=(), age_s=60, text="You've hit your weekly limit \u00b7 resets 12pm (America/New_York)"):
        """a conversation whose last turn is Claude Code's limit reply (as 2.1.330 writes it), then `then`. By default
        written a minute ago, so the reset it names is still ahead (G2.48)."""
        d = os.path.join(self.home, ".claude", "projects", "-work")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, sid + ".jsonl"), "w") as f:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - age_s)) + ".000Z"
            for e in ({"type": "user", "timestamp": stamp, "message": {"content": "carry on"}},
                      {"type": "assistant", "timestamp": stamp, "isApiErrorMessage": True, "error": "rate_limit", "message": {
                          "model": "<synthetic>", "content": [{"type": "text", "text": text}]}},
                      {"type": "system", "subtype": "informational", "level": "notice"}, {"type": "cost-state"}) + tuple(then):
                f.write(json.dumps(e) + "\n")

    def test_G2_45_no_note_into_a_conversation_that_ended_on_a_limit_and_a_note_held_for_the_reset_is_named(self):
        # seen on a real machine: a note typed at the weekly limit is not lost; Claude Code holds it and runs it
        # by itself at the reset, hours later. Alpha's conversation ended on a limit reply a minute ago, whose reset
        # is still ahead: no note for it. That reply is no evidence that the login is at its limit NOW, so Beta and Gamma, on the same
        # login, still get theirs.
        a = self.start_lane("alpha", "Alpha Lane")
        self.start_lane("beta", "Beta Lane")
        self.start_lane("gamma", "Gamma Lane")
        self.tg("record", "--force")
        self.limit_reply(a)
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 3)
        self.assertNotIn(a, [x["sid"] for x in self.typed()], out)
        self.assertEqual(len(self.typed()), 2, out)
        self.assertIn("Alpha Lane: its conversation last ended on a usage or limit reply (\"You've hit your weekly limit "
                      "\u00b7 resets 12pm (America/New_York)\"); the wake-up note is not typed", out)
        self.assertIn("Alpha Lane: loaded, but its conversation last ended on a usage or limit reply; the wake-up note "
                      "was not typed", out)
        self.assertNotIn("is at its usage limit", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((sorted(rep["noted"]), rep["note_not_typed"]), (["Beta Lane", "Gamma Lane"], ["Alpha Lane"]))
        # the same again, but now the login IS at its limit: the first note typed is seen held for the reset (live
        # evidence), and the other session on that login gets none
        self.reboot()
        open(os.path.join(self.home, "limitqueue"), "w").close()
        n, t = len(self.launches()), len(self.typed())
        rc, out = self.tg("revive")
        self.wait_launches(n + 3)
        self.assertEqual(len(self.typed()) - t, 1, out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual(len(rep["noted"]), 1, out)
        self.assertEqual(rep["note_queued"], rep["noted"])
        other = ({"Beta Lane", "Gamma Lane"} - set(rep["noted"])).pop()
        self.assertEqual(rep["note_held_login_at_limit"], [other], out)
        self.assertEqual(sorted(rep["note_not_typed"]), sorted(["Alpha Lane", other]))
        # a limit reply that the person went on past is not the end of the conversation; nor is an ordinary error
        import importlib.util
        tg = self.load_tool()
        tg.HOME = self.home
        r = {"session_id": a}
        self.assertTrue(tg.ended_on_limit(r))
        self.limit_reply(a, [{"type": "user", "message": {"content": "go on"}},
                             {"type": "assistant", "message": {"model": "claude-haiku-5-5", "content": [{"type": "text", "text": "ok"}]}}])
        self.assertEqual(tg.ended_on_limit(r), "")
        self.limit_reply(a, [{"type": "assistant", "isApiErrorMessage": True, "error": "unknown",
                              "message": {"content": [{"type": "text", "text": "API Error: 500"}]}}])
        self.assertEqual(tg.ended_on_limit(r), "")
        self.assertEqual(tg.ended_on_limit({"session_id": "no-such-session"}), "")
        # one login, one limit: what tells logins apart
        k = tg.login_key
        self.assertEqual(k({"session_id": "x"}), k({"session_id": "y"}))
        self.assertNotEqual(k({"account": "A"}), k({"account": "B"}))
        self.assertNotEqual(k({"token_print": "p1"}), k({"token_print": "p2"}))
        self.assertNotEqual(k({}), k({"config_dir": "/elsewhere"}))

    def test_G2_48_a_limit_reply_holds_the_note_only_while_its_reset_is_ahead(self):
        import datetime as D
        import importlib.util
        tg = self.load_tool()
        z = lambda w: D.datetime.fromisoformat(w.replace("Z", "+00:00")).timestamp()
        weekly, five = "You've hit your weekly limit \u00b7 resets ", "You've hit your 5-hour limit \u00b7 resets "
        # real reply texts from one weekend; the moment comes from the reply's own time and zone, never today's clock
        for text, written, reset in [
                (weekly + "12pm (America/New_York)", "2026-11-21T20:00:00Z", "2026-11-22T17:00:00Z"),   # the next noon there
                (weekly + "12pm (America/New_York)", "2026-11-22T15:00:00Z", "2026-11-22T17:00:00Z"),   # the same day
                (weekly + "Nov 24, 6pm (America/New_York)", "2026-11-22T15:00:00Z", "2026-11-24T23:00:00Z"),  # its own day
                (weekly + "Jan 2, 6pm (America/New_York)", "2026-12-30T15:00:00Z", "2027-01-02T23:00:00Z"),   # next year
                (five + "2:10pm (America/New_York)", "2026-11-22T16:00:00Z", "2026-11-22T19:10:00Z")]:
            self.assertEqual(tg.limit_reset(text, z(written)), z(reset), text)
        for text in ("Usage limit reached \u00b7 limit resets 12pm", weekly + "Nov 8, 12pm",          # no zone named
                     weekly + "12pm (Not/AZone)", "You've hit your weekly limit"):
            self.assertIsNone(tg.limit_reset(text, z("2026-11-22T16:00:00Z")), text)        # cannot be read: held
        self.assertIsNone(tg.limit_reset(weekly + "12pm (America/New_York)", 0))              # no time of its own
        # a restore: Alpha's reply reset days ago and gets its note; Beta's names a day still ahead; Gamma's names
        # no zone; neither of those two gets one
        a = self.start_lane("alpha", "Alpha Lane")
        b = self.start_lane("beta", "Beta Lane")
        c = self.start_lane("gamma", "Gamma Lane")
        self.tg("record", "--force")
        ahead = D.datetime.now(D.timezone.utc) + D.timedelta(days=3)
        self.limit_reply(a, age_s=3 * 86400)
        self.limit_reply(b, text=weekly + ahead.strftime("%b ") + str(ahead.day) + ", 6pm (UTC)")
        self.limit_reply(c, age_s=3 * 86400, text="Usage limit reached \u00b7 limit resets 12pm")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 3)
        self.assertEqual([x["sid"] for x in self.typed()], [a], out)
        self.assertRegex(out, r"Alpha Lane: its last limit reply \(\"You've hit your weekly limit \u00b7 resets 12pm "
                              r"\(America/New_York\)\"\) reset at \d{4}-\d\d-\d\d \d\d:\d\d .*; the wake-up note is typed")
        self.assertIn("Beta Lane: its conversation last ended on a usage or limit reply", out)
        self.assertIn("Gamma Lane: its conversation last ended on a usage or limit reply", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["noted"], sorted(rep["note_not_typed"])), (["Alpha Lane"], ["Beta Lane", "Gamma Lane"]))

    def test_G2_45_an_old_limit_reply_on_a_screen_holds_that_session_only(self):
        # after a resume the foot of the screen is history: Alpha's shows a limit reply from before the reset above
        # a newer short turn. Alpha's note is held; Beta and Gamma, on the same login, get theirs.
        a = self.start_lane("alpha", "Alpha Lane")
        self.start_lane("beta", "Beta Lane")
        self.start_lane("gamma", "Gamma Lane")
        self.tg("record", "--force")
        self.reboot()
        with open(os.path.join(self.home, "oldlimit"), "w") as f:
            f.write(a)
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 3)
        self.assertEqual(sorted(x["sid"] for x in self.typed()), sorted(x["sid"] for x in self.launches()[n:] if x["sid"] != a), out)
        self.assertIn("Alpha Lane: loaded, but a usage or limit notice is on its screen; the wake-up note was not typed", out)
        self.assertNotIn("is at its usage limit", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((sorted(rep["noted"]), rep["note_held_login_at_limit"]), (["Beta Lane", "Gamma Lane"], []))

    def test_G2_45_a_note_whose_words_read_as_a_limit_notice_is_refused(self):
        # the note's own words, echoed on the screen after typing, must never be taken for the limit: setup names
        # them, and a revive types the default note instead
        self.cfg({"wake_text": "Back after a restart. If you hit your weekly limit, wait for the reset."})
        rc, out = self.tg("setup", "--dry-run")
        self.assertIn("wake_text is refused: its words \"hit your weekly limit\" read as Claude Code's usage or limit notice", out)
        self.recorded_then_rebooted()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        self.assertIn("wake_text is not used: its words \"hit your weekly limit\"", out)
        typed = [x["text"] for x in self.typed()]
        self.assertEqual(len(typed), 1, out)
        self.assertNotIn("weekly limit", typed[0])
        self.assertNotIn("is at its usage limit", out)
        import importlib.util
        tg = self.load_tool()
        self.assertEqual(tg.note_problem(tg.WAKE_NOTE), "")              # the default note never reads as one
        for text in ("set to continue automatically", "Stop and wait", "see /usage-credits"):
            self.assertTrue(tg.note_problem(text), text)

    def test_G2_45_once_one_note_meets_the_limit_no_other_session_on_that_login_gets_one(self):
        # a usage limit belongs to the login: five sessions on one login, the first note is held for the reset,
        # and the other four get none, or all five would run at once at the reset
        for nm in ("alpha", "beta", "gamma", "delta", "echo"):
            self.start_lane(nm, nm.title() + " Lane")
        self.tg("record", "--force")
        self.reboot()
        open(os.path.join(self.home, "limitqueue"), "w").close()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 5)
        self.assertEqual(len(self.typed()), 1, out)
        rep = jload(self.state("last-revive.json"))
        first = rep["noted"]
        self.assertEqual((len(first), rep["note_queued"]), (1, first), out)
        self.assertIn(f"{first[0]}: the note met a usage limit; Claude Code holds it and will run it by itself at the reset", out)
        self.assertIn("this machine's own login is at its usage limit: no more notes are typed into sessions on it", out)
        self.assertIn("note not typed, its login at its usage limit: 4 session(s)", out)
        self.assertIn("note queued until the reset: 1 session(s): " + first[0], out)
        self.assertEqual(sorted(rep["note_held_login_at_limit"]), sorted(set(rep["started"]) - set(first)))
        self.assertEqual(sorted(rep["note_not_typed"]), sorted(set(rep["started"]) - set(first)))
        self.assertIn("loaded, but its login is at its usage limit; the wake-up note was not typed", out)
        rc, out = self.tg("report")
        self.assertIn("note waiting in Claude Code's queue for the limit to reset", out)
        self.assertIn("note not typed (its login is at its usage limit)", out)

    def test_G2_38_on_done_is_run_with_the_counts_when_a_restore_ends(self):
        self.script("tell", '#!/bin/sh\necho "$* | $TARDIGRADE_STARTED $TARDIGRADE_LOADED $TARDIGRADE_FAILED $TARDIGRADE_NEEDS_YOU" >> "$HOME/told.log"\n')
        self.cfg({"on_done": "tell started={started} loaded={loaded} not_loaded={not_loaded} down={down} failed={failed} needs_you={needs_you}",
                  "wake": "idle"})
        for nm in ("alpha", "beta"):
            self.start_lane(nm, nm.title() + " Lane")
        # gamma had a login of its own, which it does not get back: "needs you", which is not "failed" (seen on a
        # real Mac: a clean restore was handed on as one failure)
        self.start_lane("gamma", "Gamma Lane", extra_env={"CLAUDE_CODE_OAUTH_TOKEN": "test-only-not-a-token"})
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 3)
        self.assertIn("needs you  Gamma Lane", out)
        self.assertEqual(fread(os.path.join(self.home, "told.log")),
                         "started=2 loaded=3 not_loaded=0 down=0 failed=0 needs_you=1 | 2 3 0 1\n", out)
        self.assertIn("on_done ran (exit 0): 2 started, 3 loaded, 0 not loaded, 0 down, 0 failed, 1 need you", out)
        self.assertIn("revive: 2 started, 0 already running, 0 failed, 1 need you", out)

    def test_G2_23_with_load_max_set_a_loaded_machine_is_given_time_between_starts(self):
        self.cfg({"load_max": 5, "load_wait_s": 2})
        self.recorded_then_rebooted()
        n = len(self.launches())
        t0 = time.time()
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_LOAD=99)
        self.wait_launches(n + 1)
        self.assertIn("load is 99, above load_max 5", out)
        self.assertGreaterEqual(time.time() - t0, 2)
        self.assertEqual(jload(self.state("last-revive.json"))["started"], ["Alpha Lane"])

    def test_G2_24_after_the_starts_it_counts_loaded_and_names_what_is_not(self):
        self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        self.reboot()
        with open(os.path.join(self.home, "trustprompt"), "w") as f:
            f.write("1")                        # trust answering is off: the session stays at the question
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        self.assertIn("count: started=1 LOADED=0 UP-NOT-LOADED=1 DOWN=0", out)
        self.assertIn("UP-NOT-LOADED Alpha Lane", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["loaded"], rep["not_loaded"], rep["down_again"]), ([], ["Alpha Lane"], []))
        self.assertEqual(self.typed(), [])      # and nothing is typed into a session that has not loaded

    def test_G2_50_a_restore_that_cannot_read_the_process_list_starts_nothing_on_it(self):
        """T52, seen 2026-11-23 in a probe on 0.1.8.2 and on the 0.1.9 candidate: with ps failing, a restore started a
        second copy of a session a person had started by hand. The failed read was an empty list: "nobody holds it".
        A failed read is "cannot tell": nothing is started in that pass, it says so, and it reads again at the next.
        The read fails here at each point in turn: before the first start, between two starts, at a retry pass."""
        self.ps_breaks()
        self.cfg({"starts_at_once": 1, "retry_gap_s": 1})
        a = self.start_lane("alpha", "Alpha Lane")
        b = self.start_lane("beta", "Beta Lane")
        g = self.start_lane("gamma", "Gamma Lane")

        def restores(n, sid):          # starts of this session by the restore (not by hand) since launch n
            return [x for x in self.launches()[n:] if x["sid"] == sid and x["seen"].get("MY_SETTING") != "byhand"]

        def finish(run):
            try:
                return run.communicate(timeout=180)[0]
            finally:
                if run.poll() is None:
                    run.kill()

        # 1. before the first start: alpha was started by hand after the reboot, and the first read fails
        self.tg("record", "--force")
        self.reboot()
        self.by_hand(a, "hand-a")
        n, said = len(self.launches()), self.logged("so it starts nothing now")
        self.ps_fail()
        run = self.revive_bg("boot-1")
        try:
            self.until(lambda: self.logged("so it starts nothing now") > said + 1, "two passes with the first read failing")
            self.assertEqual(self.launches()[n:], [], "something was started on a failed read")
        finally:
            self.ps_fail(False)
        out = finish(run)
        self.assertIn("It cannot tell which sessions are already running, so it starts nothing now", out)
        self.assertIn("skip  Alpha Lane: already running on this machine", out)
        self.assertEqual(restores(n, a), [], out)
        self.assertEqual((len(restores(n, b)), len(restores(n, g))), (1, 1), out)

        # 2. between two starts: the restore's first start (alpha) makes ps fail; gamma is started by hand meanwhile
        self.tg("record", "--force")
        self.reboot()
        on = os.path.join(self.home, "psfailon")
        with open(on, "w") as f:
            f.write(a)
        n, said = len(self.launches()), self.logged("so nothing is started in this pass")
        run = self.revive_bg("boot-2")
        try:
            self.until(lambda: restores(n, a), "the restore's first start")
            self.by_hand(g, "hand-g")
            self.until(lambda: self.logged("so nothing is started in this pass") > said, "a start decided on a failed read")
        finally:
            os.remove(on)
            self.ps_fail(False)
        out = finish(run)
        self.assertIn("cannot tell whether", out)
        self.assertEqual(restores(n, g), [], f"gamma ran (by hand) while the read failed: never started again\n{out}")
        self.assertIn("Gamma Lane: something else started it meanwhile (tmux default:hand-g)", out)
        self.assertEqual((len(restores(n, a)), len(restores(n, b))), (1, 1), out)

        # 3. at a retry pass: alpha's first start ends at once; before its retry a person starts it by hand, and the
        # retry pass's read fails
        self.tg("record", "--force")
        self.reboot()
        self.cfg({"retry_gap_s": 8})
        open(os.path.join(self.home, "failonce"), "w").close()
        n, tried, said = (len(self.launches()), self.logged("Alpha Lane: not running yet (attempt 1)"),
                          self.logged("cannot tell whether Alpha Lane"))
        run = self.revive_bg("boot-3")
        try:
            self.until(lambda: self.logged("Alpha Lane: not running yet (attempt 1)") > tried, "alpha's first start to fail")
            self.by_hand(a, "hand-a3")
            self.ps_fail()
            self.until(lambda: self.logged("cannot tell whether Alpha Lane") > said, "the retry pass to read and fail")
        finally:
            self.ps_fail(False)
        out = finish(run)
        self.assertEqual(len(restores(n, a)), 1, f"alpha: its first start only (it ended at once), never again\n{out}")
        self.assertIn("Alpha Lane: something else started it meanwhile (tmux default:hand-a3)", out)

    def test_G2_50_a_restore_whose_first_read_keeps_failing_starts_nothing_and_reports_it(self):
        """T52, from the review: a restore whose first read of the process list fails for all of retry_min starts
        nothing, and says so where a person or a program looks: this boot's report (never the last boot's), and
        on_done, with the reason in TARDIGRADE_CANNOT_TELL and "unknown" for what could not be counted."""
        self.ps_breaks()
        done = os.path.join(self.home, "done")
        self.cfg({"retry_min": 0.05, "retry_gap_s": 1,
                  "on_done": f'echo "$TARDIGRADE_CANNOT_TELL|{{started}}|{{down}}" > {done}'})
        self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        self.ps_fail()
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S="100", TARDIGRADE_FAKE_BOOT_ID="boot-z")
        self.ps_fail(False)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.launches()[n:], [], out)
        self.assertIn("revive: started nothing: the process list could not be read", out)
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["boot"], rep["started"]), ("boot-z", []))
        self.assertIn("could not be read", rep["cannot_tell"])
        self.assertEqual(fread(done).strip(), "the process list could not be read (ps ended with code 1)|0|unknown")
        rc, out = self.tg("report")
        self.assertIn("nothing was started: it could not tell which sessions already run", out)
        # its header gives no count: zeros there would read like one (seen on a real machine, 2026-11-23)
        head = [x for x in out.splitlines() if x.strip().startswith("counted")]
        self.assertEqual(len(head), 1, out)
        self.assertIn("not counted: it started nothing, as it could not tell which sessions already run", head[0])
        self.assertNotRegex(head[0], r"\d+ (loaded|not loaded|down again|already running)")
        # a run whose count could not read the process list: what it started is a count; the rest is not
        jdump(dict(rep, started=["Alpha Lane"], counted_at=int(time.time())), self.state("last-revive.json"))
        head = [x for x in self.tg("report")[1].splitlines() if x.strip().startswith("counted")][0]
        self.assertIn("1 started; loaded, not loaded and down again not counted: it could not tell", head)
        self.assertNotRegex(head, r"\d+ (loaded|not loaded|down again)")

    def test_G2_50_a_start_is_decided_on_a_read_taken_after_the_load_wait(self):
        """T52, found in a trace on 2026-11-23: with load_max set, a restore read the process list, then waited for
        the load (up to load_wait_s, 120 s by default), then started. A session started by hand during that wait
        was started a second time. The wait now comes first, and the read is taken right before the start."""
        self.cfg({"load_max": 5, "load_wait_s": 4, "starts_at_once": 1})
        a = self.start_lane("alpha", "Alpha Lane")
        self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.reboot()
        n, said = len(self.launches()), self.logged("above load_max 5")
        run = self.revive_bg("boot-1", TARDIGRADE_FAKE_LOAD="99")
        try:
            self.until(lambda: self.logged("above load_max 5") > said, "the first load wait")
            self.by_hand(a, "hand-a")              # inside alpha's load wait
            out = run.communicate(timeout=180)[0]
        finally:
            if run.poll() is None:
                run.kill()
        mine = [x for x in self.launches()[n:] if x["sid"] == a and x["seen"].get("MY_SETTING") != "byhand"]
        self.assertEqual(mine, [], f"alpha was started a second time after the load wait\n{out}")
        self.assertIn("Alpha Lane: something else started it meanwhile (tmux default:hand-a)", out)


class Census(Base):
    def test_G3_1_census_names_hidden_and_down_sessions(self):
        self.start_lane("alpha", "Alpha Lane")
        b = self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.kill([x for x in self.launches() if x["sid"] == b][0]["pid"])
        time.sleep(1.5)
        rc, out = self.tg("census", "--json")
        d = json.loads(out)
        self.assertEqual((d["running"], d["hidden"], d["with_window"], d["down"]), (1, 1, 0, 1), out)
        self.assertEqual(d["sessions"]["hidden"][0]["name"], "Alpha Lane")
        self.assertEqual(d["sessions"]["down"][0]["name"], "Beta Lane")
        rc, text = self.tg("census")
        self.assertIn("HIDDEN: running, but no window shows them", text)
        self.assertIn("DOWN: on the record, not running now", text)
        self.assertIn("NO (run setup)", text)
        self.cfg({"auto_revive": True})
        self.boot_answers("enabled", "active", copy=True)          # G3.33: the yes is read from the things themselves
        self.assertIn("Comes back after a reboot: yes, by itself", self.tg("census", TARDIGRADE_FAKE_LINGER="yes")[1])
        self.assertIn("yes, after you log in (lingering is off)", self.tg("census", TARDIGRADE_FAKE_LINGER="no")[1])

    def test_G3_6_census_and_a_note_say_that_a_restore_is_running(self):
        import re
        self.script("notify-send", '#!/bin/sh\necho "$2" >> "$HOME/notes.log"\n')
        herdr = self.script("herdr", fread(FAKE_HERDR))
        os.makedirs(os.path.join(self.home, ".config", "herdr"))
        with open(os.path.join(self.home, ".config", "herdr", "herdr.sock"), "w") as f:
            f.write("")
        jdump({"workspaces": [], "tabs": [], "panes": [], "process_info": {}, "visible": {}},
              os.path.join(self.home, "herdr-state.json"))
        self.cfg({"herdr": herdr, "herdr_tabs": True, "wake": "idle", "settle_s": 3, "starts_at_once": 1})
        for n in ("alpha", "beta"):
            self.start_lane(n, n.title() + " Lane")
        self.tg("record", "--force")
        self.reboot()
        run = subprocess.Popen([sys.executable, "-I", SCRIPT, "revive", "--boot"],
                               env=dict(self.env, TARDIGRADE_FAKE_UPTIME_S="100", DISPLAY=":10"),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            end = time.time() + 20
            while time.time() < end and not os.path.exists(self.state("progress.json")):
                time.sleep(0.1)
            time.sleep(0.5)
            rc, text = self.tg("census")
            self.assertRegex(text, r"A RESTORE IS RUNNING: [01] of 2 sessions back, since \d\d:\d\d")
            self.assertEqual(json.loads(self.tg("census", "--json")[1])["restore"]["total"], 2)
            out, _ = run.communicate(timeout=120)
        finally:
            if run.poll() is None:
                run.kill()
        self.assertNotIn("A RESTORE IS RUNNING", self.tg("census")[1])        # and not once it is over
        # with all back while the watch still runs, it does not speak of "others" on their way (seen on a real Mac)
        src = fread(SCRIPT)
        i = src.index("A RESTORE IS RUNNING")
        self.assertIn('if pr["back"] < pr["total"] and not pr.get("starts_done") else', src[i:i + 400])
        self.assertIn("All are back; it is still watching them load", src[i:i + 600])
        notes = fread(os.path.join(self.home, "notes.log")).splitlines()
        self.assertEqual(len(notes), 2, out)
        self.assertTrue(notes[0].startswith("Bringing back 2 session(s)"), notes)
        self.assertTrue(notes[1].startswith("Restore done: 2 session(s) brought back, 0 need you"), notes)
        runs = [json.loads(x) for x in fread(os.path.join(self.home, "herdr-runs.jsonl")).splitlines()]
        self.assertEqual([r["body"] for r in runs if "notification" in r], notes)

    def test_G3_7_census_names_a_name_two_sessions_carry_and_a_copy_started_by_hand(self):
        self.cfg({"wake": "idle", "settle_s": 3, "starts_at_once": 1})
        ids = [self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta")]
        # as on a real machine: two sessions that share a name, and one that Claude Code has no conversation for,
        # which the restore starts new, under a new id, in its own tmux session. None of them is "by hand".
        twins = [self.start_lane(f"twin{i}", "Twin Lane") for i in (1, 2)]
        empty = self.start_lane("delta", "Delta Lane")
        with open(os.path.join(self.home, "noconv"), "w") as f:
            f.write(empty)
        self.tg("record", "--force")
        self.reboot()
        run = subprocess.Popen([sys.executable, "-I", SCRIPT, "revive", "--boot"],
                               env=dict(self.env, TARDIGRADE_FAKE_UPTIME_S="100"),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            end = time.time() + 20
            while time.time() < end and not os.path.exists(self.state("progress.json")):
                time.sleep(0.1)
            time.sleep(1.1)
            # its owner looks, does not see Beta yet, and starts a new session under its name somewhere else
            hand = self.start_lane("byhand", "Beta Lane")
            out, _ = run.communicate(timeout=120)
        finally:
            if run.poll() is None:
                run.kill()
        d = json.loads(self.tg("census", "--json")[1])
        self.assertEqual(d["same_name"]["Beta Lane"], sorted([ids[1], hand]), out)
        self.assertEqual(d["same_name"]["Twin Lane"], sorted(twins))
        self.assertIn("Delta Lane: Claude Code has no conversation for it", out)
        self.assertEqual(d["by_hand"], [{"name": "Beta Lane", "session_id": hand, "in_place_of": ids[1]}], out)
        rc, text = self.tg("census")
        self.assertIn("SAME NAME: more than one session carries it", text)
        self.assertIn("STARTED BY HAND while its session was coming back", text)
        self.assertEqual(text.count("STARTED BY HAND"), 1)                    # the real Beta is not marked

    def test_G3_16_a_session_started_new_under_a_new_id_is_not_listed_as_down(self):
        # seen on a real Mac: until the next record, census said "down: 14" for a clean restore, naming as DOWN
        # every lane the restore had started new (Claude Code had no conversation for it) under a new id
        self.cfg({"wake": "idle"})
        self.start_lane("alpha", "Alpha Lane")
        empty = self.start_lane("beta", "Beta Lane")
        with open(os.path.join(self.home, "noconv"), "w") as f:
            f.write(empty)
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.wait_launches(n + 3)
        self.assertIn("Beta Lane: Claude Code has no conversation for it", out)
        new = [x["sid"] for x in self.launches()[n:] if x["sid"] != empty and "--resume" not in x["argv"]]
        self.assertEqual(len(new), 1, out)
        d = json.loads(self.tg("census", "--json")[1])
        self.assertEqual((d["running"], d["down"]), (2, 0), d)
        self.assertEqual(d["replaced"], [{"name": "Beta Lane", "was": empty, "now": new[0]}])
        self.assertNotIn("Beta Lane", d["same_name"])
        self.assertEqual([x["name"] for x in d["not_kept"]], [])
        rc, text = self.tg("census")
        self.assertIn("STARTED NEW under a new id, in their own place (up, not down", text)
        down_part = text.split("DOWN: on the record, not running now")[-1].split("STARTED NEW")[0] \
            if "DOWN: on the record" in text else ""
        self.assertNotIn("Beta Lane", down_part)

    def test_G3_8_report_prints_the_last_restores_timeline_and_what_needs_a_person(self):
        a, b = self.start_lane("alpha", "Alpha Lane"), self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        seen = self.table()["lanes"][0]["last_seen"]
        d = os.path.join(self.home, ".claude", "projects", "-work")
        os.makedirs(d)
        with open(os.path.join(d, b + ".jsonl"), "w") as f:                    # Beta's owner ended it
            f.write(json.dumps({"type": "user", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seen + 2)) + ".000Z",
                                "message": {"content": "<command-name>/exit</command-name>"}}) + "\n")
        self.reboot()
        self.cfg({"wake": "idle"})
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_USER_UP_S=60)
        self.wait_launches(n + 1)
        p = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        before, n = fread(p), len(self.launches())
        rc, out = self.tg("report")
        self.assertEqual(rc, 0, out)
        for line in ("machine started", "user services up", "run began", "first start", "last start", "counted"):
            self.assertRegex(out, rf"\n  {line} +\w\w\w \d\d:\d\d:\d\d", line)
        self.assertIn("1 started, 1 loaded, 0 not loaded, 0 down again", out)
        self.assertIn("Still needs a person:\n  ended by its owner, not brought back:\n    Beta Lane", out)
        self.assertEqual((fread(p), len(self.launches())), (before, n))          # it reads only

    def test_G3_2_census_changes_nothing(self):
        self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        p = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        before, n = fread(p), len(self.launches())
        self.tg("census")
        self.assertEqual(fread(p), before)
        self.assertEqual(len(self.launches()), n)

    def test_G3_4_census_counts_sessions_that_wait_for_an_answer(self):
        self.start_lane("alpha", "Alpha Lane", extra_env={"FAKE_STATUS": "waiting"})
        self.start_lane("beta", "Beta Lane")
        rc, out = self.tg("census", "--json")
        self.assertEqual(json.loads(out)["waiting"], 1)
        rc, text = self.tg("census")
        self.assertIn("Waiting for you: 1", text)

    def test_G3_5_census_names_a_session_parked_at_the_trust_question(self):
        self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        self.reboot()
        for name, text in (("slow", "60"), ("trustprompt", "1")):
            with open(os.path.join(self.home, name), "w") as f:
                f.write(text)
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 1)
        time.sleep(1.5)
        rc, out = self.tg("census", "--json")
        d = json.loads(out)
        self.assertEqual((d["running"], d["waiting"], d["trust"], d["down"]), (1, 1, 1, 0), out)
        self.assertEqual(d["sessions"]["hidden"][0]["status"], "trust?")
        rc, text = self.tg("census")
        self.assertIn("asks whether to trust", text)
        self.assertNotIn("answered Yes", fread(self.state("tardigrade.log")))     # off unless switched on


    def test_G3_12_census_names_each_running_session_that_will_not_come_back_and_why(self):
        self.start_lane("alpha", "Alpha Lane")
        self.tg("record", "--force")
        time.sleep(1.1)
        self.start_lane("beta", "Beta Lane")                     # a session started by hand after the record
        d = json.loads(self.tg("census", "--json")[1])
        self.assertEqual([(x["name"], x["why"]) for x in d["not_kept"]],
                         [("Beta Lane", "started after the last record: the next record takes it")])
        out = self.tg("census")[1]
        self.assertIn("RUNNING, BUT WILL NOT COME BACK after a reboot (1):", out)
        self.cfg({"skip_tmux_sockets": [os.path.basename(self.sock)]})   # another tool brings these back
        d = json.loads(self.tg("census", "--json")[1])
        self.assertEqual(sorted((x["name"], x["why"]) for x in d["not_kept"]),
                         [(n, "its tmux socket is on skip_tmux_sockets: another tool brings it back")
                          for n in ("Alpha Lane", "Beta Lane")])
        src = fread(SCRIPT)                                      # census and record use one rule, from one place
        self.assertEqual(src.count("left_out_why(r)"), 3)          # its definition, record and census

    def test_G3_18_census_on_a_failed_read_says_it_cannot_tell(self):
        """T53, seen 2026-11-23 in a probe on 0.1.8.2 and on the 0.1.9 candidate: with ps failing, census said
        "Running: 0 ... down: 2" while both sessions ran. A person who reads "down" starts them by hand: two copies.
        On a failed read of the process list or of a tmux server's panes, census and its --json give no count."""
        self.start_lane("alpha", "Alpha Lane")
        self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        self.ps_breaks()
        for broken in ("ps", "tmux fails", "tmux does not answer"):
            if broken == "ps":
                self.ps_fail()
            else:
                self.ps_fail(False)
                self.tmux_breaks("fails" if broken == "tmux fails" else "hangs")
            rc, out = self.tg("census")
            self.assertEqual(rc, 1, out)
            self.assertIn("Cannot tell which sessions run or which are down", out, broken)
            self.assertNotIn("Running:", out, broken)
            self.assertNotRegex(out, r"down: \d", broken)
            self.assertNotIn("Alpha Lane", out, broken)
            rc, out = self.tg("census", "--json")
            d = json.loads(out)
            self.assertEqual(rc, 1, out)
            self.assertRegex(d["cannot_tell"], "could not be read|did not list its panes", broken)
            self.assertNotIn("sessions", d, broken)
            self.assertNotIn("running", d, broken)
        os.remove(os.path.join(self.home, "tmuxfail"))
        rc, out = self.tg("census")
        self.assertIn("Running: 2", out, "with every read working census counts as before")


class Setup(Base):
    def test_G5_1_the_boot_service_revives_once_and_outlives_itself(self):
        rc, out = self.tg("setup", "--print-units")
        self.assertIn("revive --boot", out)
        self.assertIn("KillMode=process", out)      # the tmux server must outlive the one-shot service
        self.assertIn("ExecStartPre=/bin/sleep 45", out)
        self.assertIn("OnUnitActiveSec=5min", out)
        self.assertIn(os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py"), out)

    def test_G5_9_setup_keeps_sessions_that_are_down_on_the_record(self):
        # T35: on a real Linux machine whose sessions had vanished together, setup's first record was a forced
        # one and moved all of them to the down list, out of reach of a revive
        self.script("systemctl", '#!/bin/sh\necho "$@" >> "$HOME/systemctl.log"\n'
                    'case "$*" in *is-enabled*) echo enabled;; *is-active*) echo active;; esac\nexit 0\n')
        self.script("loginctl", '#!/bin/sh\necho "Linger=yes"\nexit 0\n')
        for nm in ("alpha", "beta", "gamma", "delta"):
            self.start_lane(nm, nm.title() + " Lane")
        self.tg("record", "--force")
        for row in self.launches()[1:]:           # three of the four vanish together; one still runs
            self.kill(row["pid"])
        time.sleep(1)
        rc, out = self.tg("setup")
        self.assertTrue(os.path.exists(os.path.join(self.home, "systemctl.log")), out)    # only the stand-in
        t = self.table()
        self.assertEqual(sorted(r["name"] for r in t["lanes"]), ["Alpha Lane", "Beta Lane", "Delta Lane", "Gamma Lane"], out)
        self.assertEqual(t["down"], [], out)
        self.assertIn("vanished together", out)

    # ----- G3.28 (T79): only what we wrote; a person's file never stops the leave (family rules 8 and 23) -----
    # ----- G3.33 (T93): a reboot's "yes" is read from the boot service, the timer and the copy, never the setting -----
    def test_G3_33_census_says_a_reboot_brings_sessions_back_only_from_what_is_there_now(self):
        # seen 2026-11-24 on 0.1.10: an uninstall cut part way left the setting on with the boot service off or its
        # units gone, and in a home holding only the setting census said "Comes back after a reboot: yes, by itself"
        self.cfg({"auto_revive": True})
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")

        def said(**env):
            out = self.tg("census", TARDIGRADE_FAKE_LINGER="yes", **env)[1]
            line = [x for x in out.splitlines() if x.startswith("Comes back after a reboot:")]
            self.assertEqual(len(line), 1, out)
            return line[0]
        self.boot_answers(None, None, copy=False)                   # only the setting: the case seen
        line = said()
        self.assertIn("NO: the installed copy " + copy + " is not there", line)
        self.assertNotIn("yes", line)
        self.boot_answers("disabled", "inactive", copy=True)        # the cut at 0.15 s: service off, units there
        self.assertIn("NO: the boot service is disabled; the record timer is inactive (run setup)", said())
        self.boot_answers("not-found", "inactive", copy=True)       # the cut at 0.2 s: units gone, copy there
        self.assertIn("NO: the boot service is not-found; the record timer is inactive (run setup)", said())
        self.boot_answers(None, None, copy=True)                    # systemctl gives no answer: never yes, never NO
        line = said()
        self.assertIn("could not be read: systemctl gave no answer about the boot service (exit 1); systemctl gave "
                      "no answer about the record timer (exit 1)", line)
        self.assertIn(". Next: log out and in (this login has no systemd user manager to ask), then run setup again", line)
        self.assertNotIn("NO", line)
        self.assertNotIn("yes", line)
        self.boot_answers("enabled", None, copy=True)               # one part there, one unread: not yes
        self.assertIn("could not be read: systemctl gave no answer about the record timer", said())
        self.boot_answers("enabled", "active", copy=True)           # all there: yes
        self.assertIn("Comes back after a reboot: yes, by itself", said())
        d = json.loads(self.tg("census", "--json", TARDIGRADE_FAKE_LINGER="yes")[1])
        self.assertEqual(d["comes_back"], "yes, by itself")
        # the setting off still says so first, without asking anything
        os.remove(os.path.join(self.home, "systemctl.log"))
        self.cfg({"auto_revive": False})
        self.assertIn("Comes back after a reboot: NO (run setup)", said())
        self.assertFalse(os.path.exists(os.path.join(self.home, "systemctl.log")))
        # the report does not say it, so it asks nothing
        self.cfg({"auto_revive": True})
        self.tg("report")
        self.assertFalse(os.path.exists(os.path.join(self.home, "systemctl.log")))

    def test_G3_33_setup_says_set_up_only_when_the_same_read_passes(self):
        def setup():
            rc, out = self.tg("setup")
            return rc, out
        self.boot_answers("enabled", "active", copy=False)
        rc, out = setup()
        self.assertEqual(rc, 0, out)
        self.assertIn("boot service: enabled   record timer: active", out)
        self.assertIn("Windstorm Tardigrade is set up.", out)
        self.assertNotIn("NOT set up", out)
        self.boot_answers("disabled", "active", copy=True)          # systemd did not take the boot service
        rc, out = setup()
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT set up: the boot service is disabled.", out)
        self.assertNotIn("is set up.", out)
        self.boot_answers(None, None, copy=True)                    # no answer: neither "set up" nor "NOT set up"
        rc, out = setup()
        self.assertEqual(rc, 3, out)
        self.assertIn("Setup could not read whether it is set up: systemctl gave no answer about the boot service "
                      "(exit 1); systemctl gave no answer about the record timer (exit 1). So it does not say it is.", out)
        self.assertIn("So it does not say it is. Log out and in (this login has no systemd user manager to ask), "
                      "then run setup again.", out)
        self.assertNotIn("NOT set up", out)
        self.assertNotIn("is set up.", out)
        self.assertIn("boot service: no answer   record timer: no answer", out)

    def stand_in_services(self):
        """a stand-in `systemctl --user` that keeps state as systemd does: enable makes the link under <target>.wants
        (and that folder), disable removes it (and the folder when empty), --now starts or stops, and is-enabled and
        is-active answer from that state: "not-found" for a unit whose file is not there"""
        self.script("systemctl", f"#!{sys.executable}\n" + r'''
import json, os, sys
home, a = os.environ["HOME"], [x for x in sys.argv[1:] if x != "--user"]
with open(os.path.join(home, "systemctl.log"), "a") as f:
    f.write(" ".join(a) + "\n")
S = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "systemd-active.json")
try:
    active = json.load(open(S))
except (OSError, ValueError):
    active = []
ud = os.path.join(home, ".config", "systemd", "user")
P = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "systemd-plant.json")
try:
    plant = json.load(open(P))      # what a test planted with plant(): see there
except (OSError, ValueError):
    plant = {}
if plant.get("no_bus"):             # this shell cannot reach the user manager: every call fails with systemd's words
    print(plant["no_bus"], file=sys.stderr)
    sys.exit(1)
states = plant.setdefault("states", {})
verb, now, units, rc = (a or [""])[0], "--now" in a, [x for x in a[1:] if not x.startswith("-")], 0
if verb == "show":      # one unit, then the fields asked for: answered as systemd answers, a line for each
    u, units = a[1], []
    there = os.path.isfile(os.path.join(ud, u))
    on = u in active
    n = plant.setdefault("reads", {}).get(u, 0) + 1
    plant["reads"][u] = n
    flip = plant.get("flip", {}).get(u)
    if flip and n > flip[0]:
        states[u] = flip[1:]
    st = states.get(u) or (["active", "waiting" if u.endswith(".timer") else "exited"] if on else ["inactive", "dead"])
    answer = {"LoadState": "loaded" if there else "not-found", "ActiveState": st[0], "SubState": st[1],
              "KillMode": "process" if there and "revive" in u else "control-group"}
    for k, v in answer.items():
        if k in plant.get("fields", answer) and u not in plant.get("unreadable", []):
            print(f"{k}={v}")
elif verb == "reset-failed":
    for u in units:
        if states.get(u, [""])[0] == "failed" and u not in plant.get("sticky_failed", []):
            del states[u]
    units = []
elif verb == "daemon-reload":
    for u in plant.pop("wakes_on_reload", []):
        if u not in active:
            active.append(u)
    if plant.get("no_bus_after_reload"):
        plant["no_bus"] = plant.pop("no_bus_after_reload")
    plant.setdefault("deaf", []).extend(plant.pop("deaf_after_reload", []))
for u in units:
    link = os.path.join(ud, "timers.target.wants" if u.endswith(".timer") else "default.target.wants", u)
    there = os.path.isfile(os.path.join(ud, u))
    if verb == "enable":
        if not there:
            print(f"Failed to enable unit: Unit file {u} does not exist.", file=sys.stderr)
            rc = 1
            continue
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if not os.path.lexists(link):
            os.symlink(os.path.join(ud, u), link)
        if now and u not in active:
            active.append(u)
    elif verb in ("disable", "stop"):
        if verb == "disable" and not there:     # seen on systemd 255: exit 1, and with --now nothing is stopped
            print(f"Failed to disable unit: Unit file {u} does not exist.", file=sys.stderr)
            rc = 1
            continue
        if verb == "disable" and os.path.islink(link):
            os.remove(link)
            try:
                os.rmdir(os.path.dirname(link))
            except OSError:
                pass
        if (now or verb == "stop") and u not in plant.get("deaf", []):
            states.pop(u, None)
            if u in active:
                active.remove(u)
    elif verb == "is-enabled":
        word = "enabled" if there and os.path.islink(link) else "disabled" if there else "not-found"
        print(word)
        rc = 0 if word == "enabled" else 4 if word == "not-found" else 1
    elif verb == "is-active":
        print("active" if u in active else "inactive")
        rc = 0 if u in active else 3
with open(S, "w") as f:
    json.dump(active, f)
if os.path.exists(P):
    with open(P, "w") as f:
        json.dump(plant, f)
sys.exit(rc)
'''.lstrip())
        self.script("loginctl", '#!/bin/sh\necho "Linger=yes"\nexit 0\n')

    def plant(self, **answers):
        """what the stand-in systemd of stand_in_services() answers from now on (nothing given: as it is by itself):
        states={unit: [ActiveState, SubState]} for a unit until it is stopped; flip={unit: [n, ActiveState, SubState]}:
        that state after n reads of the unit; deaf=[units a stop does nothing to]; fields=[the only fields `show`
        prints]; no_bus="systemd's words": every call fails with them, exit 1; no_bus_after_reload: the same from the
        first daemon-reload on; wakes_on_reload=[units that are running again after the next daemon-reload];
        deaf_after_reload=[units a stop does nothing to from the next daemon-reload on]; unreadable=[units whose `show`
        prints nothing, exit 0]; sticky_failed=[units a reset-failed leaves listed as failed]"""
        f = os.path.join(self.bin, "systemd-plant.json")
        if answers:
            jdump(answers, f)
        elif os.path.exists(f):
            os.remove(f)

    JOBS = ("ai.thewindstorm.tardigrade-revive", "ai.thewindstorm.tardigrade-record")

    def launchd(self, domain=0, linger=0.0, refuse=(), never=(), unread=None, loaded=None):
        """a stand-in launchctl that keeps state and answers as the real one was seen to (2026-11-24, macOS 27 and 13):
        - `print gui/<uid>`: `domain`. 0 for an account logged in at the screen; 125 with "Could not print domain:
          125: Domain does not support specified action" for one that is not, and then a job's print and a bootout
          answer 125 too; any other number is a domain that is there.
        - `print gui/<uid>/<job>`: 0 for a job launchd holds, 113 "Bad request." for one it does not.
        - `bootout`: returns at once; a job whose process is still ending stays readable for `linger` seconds (seen:
          gone at the first read with a process that ends at once, 0.2 s later with one that takes time).
        - `bootstrap` over a job launchd still holds: 5 "Bootstrap failed: 5: Input/output error", nothing loaded.
        Not from a sighting, for the tool's words only: refuse=(parts of job names launchd never takes),
        never=(jobs a bootout does nothing to), unread={job: the exit code its print answers}, and the answer to a
        bootout of a job that is not loaded (3; the tool reads no answer of bootout).
        The jobs launchd holds stay as they are from plant to plant unless `loaded` names them. Every call is written
        to launchctl.log in the test's folder; the log starts empty at each plant."""
        f, log = os.path.join(self.bin, "launchd.json"), os.path.join(self.tmp, "launchctl.log")
        jobs = jload(f)["jobs"] if os.path.exists(f) else {}
        if loaded is not None:
            jobs = {x: {"loaded": True, "going": 0} for x in loaded}
        jdump({"domain": domain, "linger": linger, "refuse": list(refuse), "never": list(never),
               "unread": unread or {}, "jobs": jobs}, f)
        if os.path.exists(log):
            os.remove(log)
        self.script("launchctl", f"#!{sys.executable}\n" + r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(sys.argv[0]))
a = sys.argv[1:]
with open(os.path.join(os.path.dirname(here), "launchctl.log"), "a") as f:
    f.write(" ".join(a) + "\n")
S = os.path.join(here, "launchd.json")
with open(S) as f:
    s = json.load(f)
verb, target, now = (a + ["", ""])[0], (a + ["", ""])[1], time.time()


def end(rc, said=""):
    with open(S, "w") as f:
        json.dump(s, f)
    if said:
        print(said, file=sys.stderr)
    sys.exit(rc)


def held(label):        # a job that is loaded, or one told off whose process has not ended yet
    j = s["jobs"].get(label, {})
    return bool(j.get("loaded") or j.get("going", 0) > now)


if s["domain"] == 125:
    end(125, ("Boot-out failed" if verb == "bootout" else "Bootstrap failed" if verb == "bootstrap" else
              "Could not print domain") + ": 125: Domain does not support specified action")
if verb == "print":
    if target.count("/") == 1:
        end(s["domain"], "" if s["domain"] == 0 else "Operation not permitted")
    label = target.split("/")[-1]
    if label in s["unread"]:
        end(s["unread"][label])
    end(0) if held(label) else end(113, "Bad request.")
if verb == "bootout":
    label = target.split("/")[-1]
    if not held(label):
        end(3, "Boot-out failed: 3: No such process")
    if label not in s["never"]:
        s["jobs"][label] = {"loaded": False, "going": now + s["linger"]}
    end(0)
if verb == "bootstrap":
    label = os.path.basename(a[2])[:-len(".plist")]
    if any(x in label for x in s["refuse"]) or held(label):
        end(5, "Bootstrap failed: 5: Input/output error")
    s["jobs"][label] = {"loaded": True, "going": 0}
end(0)
'''.lstrip())

    def launchd_holds(self):
        """the jobs the stand-in launchd holds as loaded now"""
        return [k for k, v in jload(os.path.join(self.bin, "launchd.json"))["jobs"].items() if v.get("loaded")]

    def mac_tool(self, script=SCRIPT):
        """the tool loaded in this process as on a Mac, its children finding this test's stand-ins -> (the tool,
        call(function) -> (exit code, what it printed))"""
        import contextlib
        import io
        from unittest import mock
        tg = self.load_tool(script)
        patch = mock.patch.dict(os.environ, PATH=self.path)
        patch.start()
        self.addCleanup(patch.stop)
        tg.MAC, tg.ARGS, tg.DRY = True, [], False
        tg.CFG["tmux"] = TMUX                   # the settings are read in main(), which a loaded tool does not run

        def call(fn):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = fn()
            return rc, out.getvalue()
        return tg, call

    def whole_home(self):
        """{path under HOME: (kind, sha256 or '')} for every file, folder and link, hidden ones too"""
        out = {}
        for d, dirs, files in os.walk(self.home):
            for n in dirs + files:
                p = os.path.join(d, n)
                rel = os.path.relpath(p, self.home)
                if os.path.islink(p):
                    out[rel] = ("link", os.readlink(p))
                elif os.path.isdir(p):
                    out[rel] = ("dir", "")
                else:
                    with open(p, "rb") as f:
                        out[rel] = ("file", hashlib.sha256(f.read()).hexdigest())
        return out

    def persons(self, files):
        """write a person's own files (path under HOME: text); returns their listing"""
        for rel, text in files.items():
            p = os.path.join(self.home, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(text)
        return {rel: v for rel, v in self.whole_home().items() if rel in files}

    def test_G3_28_setup_revive_and_uninstall_touch_only_what_tardigrade_wrote(self):
        # seen 2026-11-23 on 0.1.9 in a scratch home: setup wrote over a person's tardigrade.py, uninstall removed
        # it and a person's __pycache__ whole, a revive removed a person's folder named lock and a person's file in
        # the launch folder
        self.stand_in_services()
        share, units = ".local/share/windstorm-tardigrade", ".config/systemd/user"
        os.makedirs(self.state(""), exist_ok=True)
        mine = self.persons({f"{share}/tardigrade.py": "# my own script, not Tardigrade\nprint('hi')\n"})
        before = self.whole_home()
        rc, out = self.tg("setup")
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT set up", out)
        self.assertIn(os.path.join(self.home, share, "tardigrade.py"), out)
        self.assertEqual(self.whole_home(), before, out)              # setup stopped before any write
        self.assertIn("Nothing was installed, and no file or folder was made.", out)
        os.rename(os.path.join(self.home, share, "tardigrade.py"), os.path.join(self.home, "my-script.py"))
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        old = time.time() - 200000                                     # older than this "boot" (uptime 100000 s)
        theirs = self.persons({
            f"{share}/notes.txt": "my notes\n",
            f"{share}/__pycache__/mine.txt": "mine\n",
            f"{units}/my-own.service": "[Unit]\nDescription=my own\n",
            ".local/state/windstorm-tardigrade/lock/diary.txt": "dear diary\n",
            ".local/state/windstorm-tardigrade/launch/my-start.sh": "echo mine\n",
            ".local/state/windstorm-tardigrade/revived-notes.txt": "mine\n",
            ".config/windstorm-tardigrade/my-settings.txt": "mine\n"})
        os.utime(self.state("lock"), (old, old))
        ours_start = self.state("launch/gone-session.sh")
        with open(ours_start, "w") as f:
            f.write("# Windstorm Tardigrade start file. Written again at each revive; safe to delete.\necho\n")
        cache = os.path.join(self.home, share, "__pycache__", "tardigrade.cpython-312.pyc")
        with open(cache, "wb") as f:
            f.write(b"\x00cache")
        # a folder named lock with a person's file in it is not a lock of ours: not taken, not removed
        rc, out = self.tg("revive")
        self.assertEqual(rc, 1, out)
        self.assertIn("not taken or removed", out)
        self.assertIn("diary.txt", out)
        self.assertEqual(self.whole_home().get(".local/state/windstorm-tardigrade/lock/diary.txt"),
                         theirs[".local/state/windstorm-tardigrade/lock/diary.txt"])
        # the person moves it; a revive's clean-up then removes only start files it wrote
        os.rename(self.state("lock"), os.path.join(self.home, "my-lock"))
        theirs = {(k if "/lock/" not in k else "my-lock/diary.txt"): v for k, v in theirs.items()}
        rc, out = self.tg("revive")
        self.assertFalse(os.path.exists(ours_start), out)
        self.assertFalse(os.path.exists(self.state("lock")), out)    # our own lock: taken and given back
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        after = self.whole_home()
        theirs["my-script.py"] = mine[f"{share}/tardigrade.py"]       # the person's script, moved by them
        for rel, v in theirs.items():
            self.assertEqual(after.get(rel), v, f"{rel}\n{out}")
        for rel in (f"{share}/tardigrade.py", f"{share}/__pycache__/tardigrade.cpython-312.pyc",
                    f"{units}/windstorm-tardigrade-revive.service", f"{units}/windstorm-tardigrade-record.service",
                    f"{units}/windstorm-tardigrade-record.timer"):
            self.assertNotIn(rel, after, out)
        self.assertNotIn("shutil.rmtree", fread(SCRIPT))               # no recursive delete anywhere

    def test_G3_28_uninstall_goes_to_its_end_with_a_persons_file_in_each_folder(self):
        # family rule 8, A FOREIGN FILE DOES NOT STOP A LEAVE: everything of ours goes, the person's files stay and
        # are named with why each folder stayed and no command, the exit code is success, and the last line says so
        self.stand_in_services()
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        share = os.path.join(self.home, ".local", "share", "windstorm-tardigrade")
        os.makedirs(os.path.join(share, "__pycache__"), exist_ok=True)
        with open(os.path.join(share, "__pycache__", "tardigrade.cpython-312.pyc"), "wb") as f:
            f.write(b"\x00cache")
        theirs = self.persons({
            ".local/share/windstorm-tardigrade/mine.txt": "mine\n",
            ".local/share/windstorm-tardigrade/__pycache__/mine.txt": "mine\n",
            ".config/systemd/user/mine.txt": "mine\n",
            ".local/state/windstorm-tardigrade/mine.txt": "mine\n",
            ".config/windstorm-tardigrade/mine.txt": "mine\n"})
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        after = self.whole_home()
        for rel, v in theirs.items():
            self.assertEqual(after.get(rel), v, f"{rel}\n{out}")
        for gone in ("tardigrade.py", "__pycache__/tardigrade.cpython-312.pyc"):
            self.assertFalse(os.path.exists(os.path.join(share, gone)), out)
        for name in ("windstorm-tardigrade-revive.service", "windstorm-tardigrade-record.service",
                     "windstorm-tardigrade-record.timer"):
            self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "systemd", "user", name)), out)
        log = fread(os.path.join(self.home, "systemctl.log"))
        self.assertIn("stop windstorm-tardigrade-record.timer\n", log)      # G5.27: stopped and read stopped first
        self.assertIn("disable windstorm-tardigrade-record.timer", log)
        self.assertNotIn("disable --now", log)
        self.assertIn("disable windstorm-tardigrade-revive.service", log)
        self.assertNotRegex(log, r"(?m)^(stop|restart|kill) .*revive")       # the boot service is never told to stop
        self.assertIn(f"Kept the folder {share}: it holds __pycache__, mine.txt.", out)
        self.assertIn(f"Kept the folder {os.path.join(share, '__pycache__')}: it holds mine.txt.", out)
        self.assertIn(os.path.join(share, "__pycache__", "mine.txt"), out)      # named, as the person's
        for cmd in ("rm ", "rmdir", "sudo", "delete", "remove them"):           # no command for what stayed
            self.assertNotIn(cmd, out.lower())
        self.assertEqual(out.strip().splitlines()[-1],
                         "Tardigrade is off this computer: nothing comes back by itself any more. What stayed is "
                         "named above.")

    def test_G3_29_nothing_setup_makes_is_writable_by_another_account_whatever_the_umask(self):
        # family rule 28 (11-23): a job gate on a shared machine started every job with umask 000; the start files
        # and units run as the person at boot, so another account must never be able to rewrite them
        self.stand_in_services()
        before = set(self.whole_home())
        for n in (1, 2):
            r = subprocess.run(["/bin/sh", "-c", 'umask 000; exec "$0" -I "$1" setup', sys.executable, SCRIPT],
                               capture_output=True, text=True, env=self.env, timeout=240)
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 0, f"run {n}\n{out}")          # the second run is not refused
            self.assertIn("is set up", out)
        made = set(self.whole_home()) - before
        self.assertIn(".local/share/windstorm-tardigrade/tardigrade.py", made)
        self.assertIn(".config/systemd/user/windstorm-tardigrade-revive.service", made)
        for rel in sorted(made):
            p = os.path.join(self.home, rel)
            mode = os.lstat(p).st_mode & 0o777
            if not os.path.islink(p):
                self.assertEqual(mode & 0o022, 0, f"{rel} is {oct(mode)}")
        for d, dirs, files in os.walk(self.home):             # and nothing at all under the home is world-writable
            for n in dirs + files:
                p = os.path.join(d, n)
                if not os.path.islink(p):
                    self.assertEqual(os.lstat(p).st_mode & 0o002, 0, p)
        # what an earlier setup under umask 000 left open (0.1.9 did) is closed by the next setup
        opened = [".local/share/windstorm-tardigrade", ".local/share/windstorm-tardigrade/tardigrade.py",
                  ".config/systemd/user/windstorm-tardigrade-revive.service",
                  ".config/systemd/user/windstorm-tardigrade-record.service",
                  ".config/systemd/user/windstorm-tardigrade-record.timer"]
        for rel in opened:
            os.chmod(os.path.join(self.home, rel), 0o777 if rel.endswith("tardigrade") else 0o666)
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        for rel in opened:
            self.assertEqual(os.stat(os.path.join(self.home, rel)).st_mode & 0o022, 0, rel)

    def test_G3_29_the_folders_above_what_runs_are_named_with_their_command_and_not_changed(self):
        # family rule 28, A FILE IS ONLY AS CLOSED AS THE FOLDERS ABOVE IT: 0.1.9's setup under umask 000 made the
        # folders above its units and copy 777; in such a folder a closed file is replaced by deleting it
        self.stand_in_services()
        os.chmod(self.tmp, 0o755)                             # a home other accounts can pass into, as most are
        for d, dirs, _ in os.walk(self.home):                 # a home with nothing open, as a person's would be
            for x in [d] + [os.path.join(d, n) for n in dirs]:
                os.chmod(x, os.stat(x).st_mode & ~0o022)
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("another account can write", out)
        # the home 0.1.9's setup left when started under umask 000 (seen 2026-11-23 in a scratch home)
        # the copy's, then the units', then the state folder's, where revive writes the start files it runs (the
        # state folder left open was seen 2026-11-24 on a stock Linux machine after 0.1.9's setup under umask 000)
        above = [".local", ".local/share", ".config", ".config/systemd", ".config/systemd/user", ".local/state"]
        for rel in above + [".local/share/windstorm-tardigrade"]:
            os.chmod(os.path.join(self.home, rel), 0o777)
        for rel in (".local/share/windstorm-tardigrade/tardigrade.py", ".config/systemd/user/windstorm-tardigrade-revive.service",
                    ".config/systemd/user/windstorm-tardigrade-record.service",
                    ".config/systemd/user/windstorm-tardigrade-record.timer"):
            os.chmod(os.path.join(self.home, rel), 0o666)
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        for i, rel in enumerate(above, 1):
            p = os.path.join(self.home, rel)
            self.assertIn(f"{i}. {p} can be written by every account on this computer: anything in it, what Tardigrade "
                          f"runs at boot included, can be replaced by another account. To close it: chmod go-w {p}", out)
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o777, rel)      # not ours to change: named, left as it is
        self.assertNotIn(f"{os.path.join(self.home, '.local/share/windstorm-tardigrade')} can be written", out)
        self.assertEqual(os.stat(os.path.join(self.home, ".local/share/windstorm-tardigrade")).st_mode & 0o022, 0)
        rc, out = self.tg("census")
        self.assertIn(f"5. {os.path.join(self.home, '.config/systemd/user')} can be written by every account", out)
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_BOOT_ID="boot-b")
        self.assertIn(f"revive --boot: 1. {os.path.join(self.home, '.local')} can be written", out)
        # the person runs the one command each line gives; then nothing more is said
        for rel in above:
            r = subprocess.run(["/bin/sh", "-c", f"chmod go-w {os.path.join(self.home, rel)}"])
            self.assertEqual(r.returncode, 0)
        rc, out = self.tg("census")
        self.assertNotIn("another account can write", out)
        self.assertIn('"open_folders": []', self.tg("census", "--json")[1])
        # reach: a home at 700 keeps every other account out, so nothing under it is named, open or not
        os.chmod(os.path.join(self.home, ".local"), 0o777)
        os.chmod(self.home, 0o700)
        self.assertNotIn("another account can write", self.tg("census")[1])
        os.chmod(self.home, 0o755)
        self.assertIn(f"1. {os.path.join(self.home, '.local')} can be written by every account", self.tg("census")[1])
        os.chmod(os.path.join(self.home, ".local"), 0o755)
        # the settings and state folders, and the start files' folder in it, are the tool's own: each run closes the
        # first two to 0700, which keeps the third out of reach, so none of them is named; the folders above them are
        for rel in (".config/windstorm-tardigrade", ".local/state/windstorm-tardigrade",
                    ".local/state/windstorm-tardigrade/launch"):
            os.makedirs(os.path.join(self.home, rel), exist_ok=True)
            os.chmod(os.path.join(self.home, rel), 0o777)
        self.assertNotIn("another account can write", self.tg("census")[1])
        for rel in (".config/windstorm-tardigrade", ".local/state/windstorm-tardigrade"):
            self.assertEqual(os.stat(os.path.join(self.home, rel)).st_mode & 0o777, 0o700, rel)

    def test_G3_29_a_group_folder_is_named_only_when_another_member_can_reach_it(self):
        # family rule 28, WHEN A PERSON'S FOLDER IS NAMED: a 775 folder in the person's own group with nobody else in
        # it is the ordinary state of a Linux home; a second member who can pass the home makes it open
        import importlib.util
        tg = self.load_tool()
        real_account_ids = tg.account_ids
        os.chmod(self.tmp, 0o755)
        d = os.path.join(self.home, ".local")
        os.makedirs(d, exist_ok=True)
        os.chmod(self.home, 0o750)
        os.chmod(d, 0o775)
        me, gid = __import__("pwd").getpwuid(os.getuid()).pw_name, os.stat(d).st_gid
        tg.group_accounts = lambda g: {me}                                   # a group of one
        self.assertEqual(tg.who_can_write(d), "")
        tg.group_accounts = lambda g: {me, "second-name"}                     # family rule 28: an account is its
        tg.account_ids = lambda n: (os.getuid(), {gid})                        # number; a second name for the person's
        self.assertEqual(tg.who_can_write(d), "")                             # own number is the person
        tg.group_accounts = lambda g: {me, "friend"}                          # a second member
        tg.account_ids = lambda n: (os.getuid() + 54321, {gid})
        self.assertEqual(tg.who_can_write(d), "the account friend (in the folder's group)")
        os.chmod(self.home, 0o700)                                           # who cannot pass the home cannot reach it
        self.assertEqual(tg.who_can_write(d), "")
        os.chmod(self.home, 0o750)
        def unreadable(g):
            raise KeyError(g)
        tg.group_accounts = unreadable                                        # membership that cannot be read is said
        self.assertIsNone(tg.who_can_write(d))
        # a Mac's "nobody" group is 4294967294, which os.getgrouplist refuses with OverflowError (seen on macOS 13 and
        # 27, Python 3.9): passed as its negative form it answers; one account that still cannot be read is said
        calls = []
        def getgrouplist(name, gid):
            calls.append(gid)
            if gid > 2 ** 31 - 1:
                raise OverflowError("Python int too large to convert to C int")
            return [gid, gid % 2 ** 32 if gid < 0 else gid]
        real_ggl, real_ids = os.getgrouplist, real_account_ids
        try:
            os.getgrouplist = getgrouplist
            pwd_mod = __import__("pwd")
            real_getpwnam = pwd_mod.getpwnam
            pwd_mod.getpwnam = lambda n: type("P", (), {"pw_uid": os.getuid() + 777, "pw_gid": 4294967294})()
            uid, gids = real_ids("nobody-group-user")
            self.assertEqual(calls, [-2])
            self.assertEqual(gids, {4294967294})
            tg.group_accounts = lambda g: {me, "x"}
            tg.account_ids = lambda n: (_ for _ in ()).throw(OverflowError("too large"))
            self.assertIsNone(tg.who_can_write(d))                           # said, no traceback
            tg.open_folders = lambda: [(d, tg.who_can_write(d))]
            self.assertIn("could not be read", tg.open_folder_lines()[1])
        finally:
            os.getgrouplist = real_ggl
            pwd_mod.getpwnam = real_getpwnam
        # a folder another account owns: named with its owner, and no command, since the person cannot change it
        tg.open_folders = lambda: [(d, "the account that owns it")]
        lines = tg.open_folder_lines()
        self.assertIn(f"1. {d} belongs to another account", lines[1])
        self.assertIn("Only that account or an administrator can close it.", lines[1])
        self.assertNotIn("chmod", " ".join(lines))
        # a folder root owns that others can write: root named as its owner, and no command the person cannot run
        tg.open_folders = lambda: [(d, "every account on this computer")]
        tg.folder_owner = lambda x: 0
        lines = tg.open_folder_lines()
        self.assertIn(f"1. {d} belongs to root, the administrator, and every account on this computer can write it", lines[1])
        self.assertIn("only an administrator can close it", lines[1])
        self.assertNotIn("chmod", " ".join(lines))

    def test_G5_15_the_suite_refuses_to_run_the_tool_outside_its_own_scratch_home(self):
        # the suite's guard (T78, family rule 23): only T78's own test may run the tool with the test setting ignored,
        # and no test may run it with a home outside its own folder; a refused program never starts
        import pwd
        real = pwd.getpwuid(os.getuid()).pw_dir
        # the run's own home (the guard's first part): set before anything loaded the tool, handed to what the run
        # starts, with no line to this account's desktop session or tmux server, and no git config of the account
        self.assertEqual(os.environ["HOME"], os.path.join(RUN_HOME, "home"))
        self.assertNotEqual(os.path.realpath(os.environ["HOME"]), os.path.realpath(real))
        for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "TMUX"):
            self.assertNotIn(k, os.environ)
        child = subprocess.run(["/bin/sh", "-c", 'echo "$HOME|$XDG_RUNTIME_DIR|$TMUX"'], capture_output=True, text=True)
        self.assertEqual(child.stdout.strip(), os.path.join(RUN_HOME, "home") + "||")
        git = subprocess.run(["git", "config", "--global", "--list"], capture_output=True, text=True)
        self.assertEqual(git.stdout, "")
        probe = os.path.join(self.tmp, "started")
        refused = [
            lambda: subprocess.run([sys.executable, "-I", SCRIPT, "--version"], env=dict(self.env, HOME=real)),
            lambda: subprocess.run([sys.executable, "-I", SCRIPT, "--version"]),            # the runner's own HOME
            lambda: subprocess.run(["/bin/sh", "-c", f"touch {probe}; exec python3 -I {SCRIPT} --version"],
                                   env=dict(self.env, HOME=real)),
            lambda: subprocess.run([sys.executable, "-I", SCRIPT, "--version"], env=dict(self.env, TARDIGRADE_TESTING="0")),
            lambda: importlib.util.spec_from_file_location("tardigrade", SCRIPT).loader.exec_module(
                importlib.util.module_from_spec(importlib.util.spec_from_file_location("tardigrade", SCRIPT))),
        ]
        for i, run in enumerate(refused):
            with self.assertRaises(GuardRefused, msg=i):
                run()
        self.assertFalse(os.path.exists(probe))                    # refused before it started
        mark = os.path.join(self.home, "tardigrade-test-home")
        os.rename(mark, mark + ".off")                              # the setting with no marker: ignored, so refused
        with self.assertRaises(GuardRefused):
            self.tg("--version")
        os.rename(mark + ".off", mark)
        tmp = GUARD["tmp"]
        GUARD["tmp"] = None                                          # outside a test that made its own folder
        with self.assertRaises(GuardRefused):
            self.tg("--version")
        GUARD["tmp"] = tmp
        rc, out = self.tg("--version")                              # this test's own home, marker and setting: runs
        self.assertEqual((rc, out.strip()), (0, re.search(r'^VERSION = "([0-9.]+)"', fread(SCRIPT), re.M).group(1)))
        self.assertTrue(self.load_tool().TESTING)
        self.assertEqual(len(GUARD["caught"]), 7)
        GUARD["caught"].clear()                                      # made on purpose, so they do not fail this test

    def test_G5_15_the_testing_setting_alone_changes_nothing_and_says_so(self):
        # T78, family rule 27: two keys for test mode, the setting AND the marker the suite writes in its scratch home
        self.stand_in_services()
        mark = os.path.join(self.home, "tardigrade-test-home")
        # with the marker: the test behaviour (a stand-in fact: this boot's id)
        rc, out = self.tg("setup", TARDIGRADE_FAKE_BOOT_ID="boot-marked")
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(self.state("revived-boot-marked")), out)
        self.assertNotIn("ignored it", out)
        # the setting alone: no stand-in fact, and the first line of the run says it was found and ignored
        os.remove(mark)
        r = subprocess.run([sys.executable, "-I", SCRIPT, "census", "--json"], capture_output=True, text=True,
                           env=dict(self.env, TARDIGRADE_FAKE_BOOT_ID="boot-unmarked"), timeout=240)
        self.assertTrue(r.stderr.startswith("found TARDIGRADE_TESTING and ignored it: there is no test marker"), r.stderr)
        json.loads(r.stdout)                                    # the JSON stays whole
        self.assertIn("found TARDIGRADE_TESTING and ignored it", fread(self.state("tardigrade.log")))
        rc, out = self.tg("setup", TARDIGRADE_FAKE_BOOT_ID="boot-unmarked")
        self.assertFalse(os.path.exists(self.state("revived-boot-unmarked")), out)
        with open("/proc/sys/kernel/random/boot_id") as f:
            self.assertTrue(os.path.exists(self.state("revived-" + f.read().strip())), out)
        # and nothing hands it on: not to a tmux server, not to a session's start file
        import importlib.util
        old = dict(os.environ)
        try:
            os.environ.update(HOME=self.home, TARDIGRADE_TESTING="1")
            for marked in (False, True):
                if marked:
                    with open(mark, "w") as f:
                        f.write(MARK_TEXT + "\n")
                spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
                tg = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(tg)
                tg.load_cfg()
                self.assertEqual(tg.TESTING, marked)
                self.assertEqual("TARDIGRADE_TESTING" in tg.clean_env(), marked)
                self.assertEqual("TARDIGRADE_TESTING" in tg.server_env(), marked)
        finally:
            os.environ.clear()
            os.environ.update(old)
        # a marker that is not the suite's (a link, or other words) is no key
        os.remove(mark)
        os.symlink(os.path.join(self.tmp, "elsewhere"), mark)
        with open(os.path.join(self.tmp, "elsewhere"), "w") as f:
            f.write(MARK_TEXT + "\n")
        self.assertIn("ignored it", self.tg("census")[1])
        os.remove(mark)
        with open(mark, "w") as f:
            f.write("something else\n")
        self.assertIn("ignored it", self.tg("census")[1])

    def test_G5_2_setup_dry_run_installs_nothing(self):
        rc, out = self.tg("setup", "--dry-run")
        self.assertIn("would copy", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "systemd")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local", "share", "windstorm-tardigrade")))

    def test_G5_3_setup_and_census_say_who_has_to_log_in_on_a_mac(self):
        import importlib.util
        tg = self.load_tool()
        short, lines = tg.mac_story("FileVault is On.", "me", "me")
        self.assertIn("unlocks this Mac (FileVault)", short)
        self.assertIn("NOTHING comes back", lines[0])
        short, lines = tg.mac_story("FileVault is Off.", "me", "me")
        self.assertEqual(short, "yes, by itself (this Mac logs you in)")
        short, lines = tg.mac_story("FileVault is Off.", "tester", "me")     # it logs in by itself, as someone else
        self.assertIn("only after YOU log in at its screen", short)
        self.assertIn("as another user, not as you", lines[0])
        short, lines = tg.mac_story("FileVault is Off.", "", "me")
        self.assertEqual(short, "after you log in at its screen")
        self.assertIn("A login over ssh does not start it", lines[-1])

    def test_G5_17_on_a_mac_setup_asks_launchd_before_its_first_write_and_names_what_a_refused_job_left(self):
        # seen 2026-11-24 on a Mac account with no login at its screen (launchctl print gui/<uid>: exit 125): setup
        # wrote its copy and the revive job's file, then said only "launchd did not take ..." (T83)
        import contextlib
        import io
        from unittest import mock
        tg = self.load_tool()
        patch = mock.patch.dict(os.environ, PATH=self.path)     # the tool's children find this test's launchctl
        patch.start()
        self.addCleanup(patch.stop)
        tg.MAC, tg.ARGS, tg.DRY = True, [], False
        tg.CFG["tmux"] = TMUX                   # the settings are read in main(), which a loaded tool does not run
        uid, log = os.getuid(), os.path.join(self.tmp, "launchctl.log")
        la = os.path.join(self.home, "Library", "LaunchAgents")
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")
        revive, record = (os.path.join(la, f"ai.thewindstorm.tardigrade-{x}.plist") for x in ("revive", "record"))

        def setup():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = tg.setup()
            return rc, out.getvalue()
        # (a) no login at the screen: the stop comes before the first write
        self.launchd(domain=125)
        before = self.whole_home()
        for dry in (False, True):
            tg.DRY = dry
            rc, out = setup()
            self.assertEqual(rc, 1, out)
            self.assertIn("NOT set up: this account is not logged in at this Mac's screen", out)
            self.assertIn(f'`launchctl print gui/{uid}` answered exit 125 ("Could not print domain: 125: Domain does '
                          'not support specified action").', out)
            self.assertIn("Nothing was installed, and no file or folder was made.", out)
            self.assertTrue(out.rstrip().endswith("Log in at this Mac's screen as this user, then run setup again."), out)
            self.assertNotIn("would copy", out)
            self.assertEqual(self.whole_home(), before)
            self.assertEqual(fread(log).splitlines(), [f"print gui/{uid}"])     # asked, and nothing else tried
            os.remove(log)
        tg.DRY = False
        # (b) any other answer is not a "no": setup goes on; a job launchd then refuses is named with what is on disk
        self.launchd(domain=1, refuse=("tardigrade-revive",))
        rc, out = setup()
        self.assertEqual(rc, 1, out)
        self.assertIn('NOT set up: launchd did not take ai.thewindstorm.tardigrade-revive (exit 5: "Bootstrap failed: '
                      '5: Input/output error"). Nothing comes back by itself yet.\nRead after that: launchd does not '
                      'have it.\n', out)
        self.assertIn("they are on disk:\n  " + copy + "\n  " + revive + "\n", out)
        self.assertNotIn(record, out)
        self.assertNotIn("launchd took", out)
        self.assertTrue(os.path.exists(copy) and os.path.exists(revive) and not os.path.exists(record))
        self.assertIn("at your next login at this Mac's screen", out)
        self.assertIn("Run setup again: it ends by reading what is loaded. To take them off, run uninstall.", out)
        # the second job refused after the first loaded: the line says which one launchd holds
        self.launchd(refuse=("tardigrade-record",))
        rc, out = setup()
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT set up: launchd did not take ai.thewindstorm.tardigrade-record (exit 5", out)
        self.assertIn("launchd took ai.thewindstorm.tardigrade-revive before that, and it stays loaded.", out)
        self.assertIn("they are on disk:\n  " + copy + "\n  " + revive + "\n  " + record + "\n", out)
        self.assertTrue(os.path.exists(record))
        # and the census says what is true in that state, with the setting on from an earlier setup or not
        self.assertEqual(tg.comes_back(), "NO (run setup)")
        tg.CFG["auto_revive"] = True
        self.assertEqual(tg.comes_back(), "NO: launchd has not loaded ai.thewindstorm.tardigrade-record (run setup)")

    def test_G5_22_on_linux_setup_asks_the_user_manager_before_its_first_write(self):
        # seen 2026-11-24 on a stock VM, in an account with no user manager: setup copied itself in and wrote its
        # units before it said it could not read whether it was set up (T95)
        log = os.path.join(self.tmp, "asked.log")
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")

        def systemctl(stderr):
            self.script("systemctl", f'#!/bin/sh\necho "$@" >> "{log}"\necho "{stderr}" >&2\nexit 1\n')
        systemctl("Failed to connect to bus: No medium found")
        self.tg("census")       # every command makes the settings and state folders: made here, before the listing
        before = self.whole_home()
        # systemd's own words for it differ by version, and both were seen (systemd 255, then 262: G5.27)
        for said in ("Failed to connect to bus: No medium found",
                     "Failed to connect to user scope bus via local transport: No such file or directory"):
            systemctl(said)
            for args in (("setup",), ("setup", "--dry-run")):
                os.remove(log) if os.path.exists(log) else None
                rc, out = self.tg(*args)
                self.assertEqual(rc, 1, out)
                self.assertIn("NOT set up: this login has no systemd user manager to hand the boot service to. `systemctl "
                              f'--user` answered "{said}" (exit 1).', out)
                self.assertIn("Nothing was installed, and no file or folder was made.", out)
                self.assertTrue(out.rstrip().endswith("Log out and in (this login has no systemd user manager to ask), "
                                                      "then run setup again."), out)
                self.assertNotIn("would copy", out)
                self.assertEqual(self.whole_home(), before)
                self.assertEqual(fread(log).splitlines(), ["--user is-enabled windstorm-tardigrade-revive.service"])
        # any other answer is not a "no": setup goes on as before (here to its own "could not read", exit 3)
        systemctl("Failed to get unit file state for windstorm-tardigrade-revive.service: Access denied")
        rc, out = self.tg("setup")
        self.assertEqual(rc, 3, out)
        self.assertNotIn("has no systemd user manager to hand", out)
        self.assertTrue(os.path.exists(copy))

    def test_G5_23_setup_writes_the_settings_file_only_when_a_value_changes(self):
        # seen 2026-11-24 on an upgrade from 0.1.8.1: a settings file laid out by hand came out of setup sorted, with
        # a final newline, though no value had changed (T96)
        p = os.path.join(self.home, ".config", "windstorm-tardigrade", "config.json")
        d = json.loads(fread(p))
        hand = dict(sorted(d.items(), reverse=True), auto_revive=True)

        def lay(d):
            with open(p, "w") as f:
                f.write(json.dumps(d, indent=4))        # a person's layout: four spaces, their own order, no last newline
            return fread(p)
        laid = lay(hand)
        self.assertNotEqual(laid, json.dumps(hand, indent=1, sort_keys=True) + "\n")
        self.boot_answers("enabled", "active", copy=False)
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.assertEqual(fread(p), laid)                # byte for byte
        rc, out = self.tg("setup")                      # and over its own install
        self.assertEqual(rc, 0, out)
        self.assertEqual(fread(p), laid)
        # a value that does change is written, and every other value stays
        lay(dict(hand, auto_revive=False))
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(fread(p)), hand)
        self.assertNotEqual(fread(p), laid)

    def test_G3_30_a_command_that_only_reads_makes_no_folder_and_no_file(self):
        """T81: a census, a report, a dry run and a refused setup leave a home exactly as they found it: the whole
        home is listed before and after each (every file, folder and link, with what each holds)"""
        shutil.rmtree(os.path.join(self.home, ".config"))      # the suite's own settings file: this home has none

        def listing():      # the whole home, less the one file the suite's stand-in systemctl writes about itself
            return {k: v for k, v in self.whole_home().items() if k != "systemctl.log"}
        before = listing()
        self.assertNotIn(".config", before)
        self.assertNotIn(".local", before)
        for a in (["census"], ["status"], ["census", "--json"], ["report"], ["trust"], ["setup", "--dry-run"],
                  ["revive", "--dry-run"], ["record", "--dry-run"], ["uninstall", "--dry-run"]):
            rc, out = self.tg(*a)
            self.assertEqual(listing(), before, f"`{' '.join(a)}` (exit {rc}) changed the home:\n{out}")
        # setup refused because no user manager answers (G5.22): the stop line no longer has folders to excuse
        self.script("systemctl", '#!/bin/sh\necho "Failed to connect to bus: No medium found" >&2\nexit 1\n')
        for a in (["setup"], ["setup", "--dry-run"]):
            rc, out = self.tg(*a)
            self.assertEqual(rc, 1, out)
            self.assertIn("Nothing was installed, and no file or folder was made.", out)
            self.assertNotIn("state folders", out)
            self.assertEqual(listing(), before, f"a refused `{' '.join(a)}` changed the home:\n{out}")
        # setup refused because a person's file is where the copy goes (G3.28): only their file is there after
        self.boot_answers("enabled", "active", copy=False)
        share = os.path.join(self.home, ".local", "share", "windstorm-tardigrade")
        os.makedirs(share)
        with open(os.path.join(share, "tardigrade.py"), "w") as f:
            f.write("# a person's own file of that name\n")
        theirs = listing()
        rc, out = self.tg("setup")
        self.assertEqual(rc, 1, out)
        self.assertIn("Nothing was installed, and no file or folder was made.", out)
        self.assertEqual(listing(), theirs, out)
        # and the first command that does write makes the two folders, closed to others
        os.remove(os.path.join(share, "tardigrade.py"))
        rc, out = self.tg("record")
        self.assertEqual(rc, 0, out)
        for d in (os.path.join(self.home, ".config", "windstorm-tardigrade"),
                  os.path.join(self.home, ".local", "state", "windstorm-tardigrade")):
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o700, d)

    def test_G5_11_below_python_3_9_every_command_stops_first_names_the_version_and_makes_nothing(self):
        """T58: on macOS 13 the python3 of Apple's developer tools is 3.9.6, and nothing said which Python the tool
        needs; an older one got a SyntaxError at a line deep in the file"""
        src = fread(SCRIPT)
        tree = ast.parse(src)
        self.assertIsInstance(tree.body[0], ast.Expr, "the file's own words first")
        self.assertEqual([type(x).__name__ for x in tree.body[1:5]], ["Import", "Assign", "FunctionDef", "If"],
                         "the check comes before anything else is loaded or made")
        self.assertEqual([a.name for a in tree.body[1].names], ["sys"], "nothing but sys is loaded before the check")
        self.assertEqual(ast.literal_eval(tree.body[2].value), (3, 9), "the floor")
        self.assertEqual(tree.body[3].name, "too_old")
        self.assertIn("sys.exit(2)", ast.get_source_segment(src, tree.body[4]))
        ast.parse(src, feature_version=(3, 6))          # an older python3 can read the file as far as the check
        self.assertNotIn(":=", src)
        tg = self.load_tool()
        self.assertIsNone(tg.too_old((3, 9, 0)))
        self.assertIsNone(tg.too_old((3, 14, 5)))
        self.assertIsNone(tg.too_old(sys.version_info))
        for old in ((3, 8, 18), (3, 6, 9), (2, 7, 18)):
            said = tg.too_old(old)
            self.assertIn("Python " + ".".join(map(str, old)), said)
            self.assertIn("Python 3.9 or newer", said)
            self.assertIn("Nothing was made or changed", said)

        def listing():
            out = {}
            for base, dirs, files in os.walk(self.tmp):
                for n in dirs + files:
                    q = os.path.join(base, n)
                    st = os.lstat(q)
                    out[os.path.relpath(q, self.tmp)] = (st.st_mode, st.st_size, st.st_mtime_ns)
            return out

        def every_command_stops(python, script, found, floor):
            cmds = [[c] for c in sorted(tg.COMMANDS)] + [[], ["--version"], ["--help"], ["revive", "--boot"],
                                                         ["setup", "--dry-run"], ["no-such-command"]]
            for a in cmds:
                before = listing()
                r = subprocess.run([python, "-I", script, *a], capture_output=True, text=True, env=self.env, timeout=60)
                self.assertEqual((r.returncode, r.stdout), (2, ""), f"{python} {a}: {r.stderr[-300:]}")
                self.assertIn(f"is Python {found}", r.stderr, a)
                self.assertIn(f"runs on Python {floor} or newer", r.stderr, a)
                self.assertEqual(len(r.stderr.strip().splitlines()), 1, f"one line, nothing else: {r.stderr[-300:]}")
                self.assertEqual(listing(), before, f"{python} {a}: something was made or changed")

        # on every machine: this Python against a copy of the tool whose floor is above it, so the stop itself runs
        above = os.path.join(self.tmp, "above", "tardigrade.py")
        os.makedirs(os.path.dirname(above))
        self.assertEqual(src.count("FLOOR = (3, 9)"), 1)
        with open(above, "w") as f:
            f.write(src.replace("FLOOR = (3, 9)", "FLOOR = (9, 9)"))
        every_command_stops(sys.executable, above, ".".join(map(str, sys.version_info[:3])), "9.9")
        # with a real older python3 where one is found; CI brings one, and must
        older = os.environ.get("TARDIGRADE_TEST_OLDER_PYTHON") or next(
            (w for w in (shutil.which(f"python3.{m}") for m in (8, 7, 6)) if w), None)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            self.assertTrue(older, "CI's own older python3 was not found: the stop was not seen on a real one")
        if older:
            v = subprocess.run([older, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                               capture_output=True, text=True).stdout.strip()
            self.assertLess(tuple(int(x) for x in v.split(".")[:2]), (3, 9), f"{older} is not an older one")
            every_command_stops(older, SCRIPT, v, "3.9")
        # said where a person and an agent read, and run in CI at the floor
        self.assertIn("Python 3.9 or newer", fread(os.path.join(ROOT, "README.md")))
        self.assertIn("Python 3.9 or newer", fread(os.path.join(ROOT, "skills", "tardigrade", "SKILL.md")))
        wf = fread(os.path.join(ROOT, ".github", "workflows", "tests.yml"))
        matrix = re.findall(r'(?m)^        python: \[(.*?)\]', wf)
        self.assertEqual(len(matrix), 1)
        runs = [tuple(int(x) for x in v.strip().strip('"').split(".")) for v in matrix[0].split(",")]
        self.assertEqual(min(runs), (3, 9), "CI runs the suite at the floor")
        self.assertRegex(wf, r"python-version: \|\n\s+3\.8\n\s+\$\{\{ matrix\.python \}\}\n", "CI brings an older python3")

    def test_G2_52_setup_and_each_boot_run_make_every_marker_they_keep_private(self):
        # T69: seen on a real Mac upgraded to 0.1.9 and restarted: the markers it wrote were 0600, the three it kept
        # from earlier versions stayed readable by others (0644)
        self.stand_in_services()
        os.makedirs(self.state(""), exist_ok=True)

        def plant(names, mode):
            for i, n in enumerate(names):
                with open(self.state(n), "w") as f:
                    f.write("x\n")
                os.chmod(self.state(n), mode)
                os.utime(self.state(n), (time.time() - 3600 + i * 60,) * 2)

        def modes():
            return {f: os.stat(self.state(f)).st_mode & 0o7777 for f in os.listdir(self.state("")) if f.startswith("revived-")}

        plant(["revived-old-a", "revived-old-b", "revived-old-c"], 0o644)
        plant(["revived-notes.txt"], 0o644)             # not a marker by its name: the person's own file
        rc, out = self.tg("setup", TARDIGRADE_FAKE_BOOT_ID="boot-one")
        self.assertEqual(rc, 0, out)
        self.assertEqual(modes(), {"revived-old-a": 0o600, "revived-old-b": 0o600, "revived-old-c": 0o600,
                                   "revived-boot-one": 0o600, "revived-notes.txt": 0o644}, out)
        # and a boot run does the same for what it keeps: widened again, and one that only the group could read
        for n, m in (("revived-old-a", 0o664), ("revived-old-b", 0o640), ("revived-boot-one", 0o644)):
            os.chmod(self.state(n), m)
        self.reboot()
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_BOOT_ID="boot-two")
        self.assertEqual(modes(), {"revived-old-a": 0o600, "revived-old-b": 0o600, "revived-old-c": 0o600,
                                   "revived-boot-one": 0o600, "revived-boot-two": 0o600, "revived-notes.txt": 0o644}, out)

    def empty_home(self):
        """this test's home with no settings file of the suite's, and its listing less the one file the suite's
        stand-in systemctl writes about itself"""
        shutil.rmtree(os.path.join(self.home, ".config"))
        self.stand_in_services()
        return lambda: {k: v for k, v in self.whole_home().items() if k != "systemctl.log"}

    def test_G3_26_setup_keeps_the_list_of_folders_it_made_and_uninstall_takes_the_empty_ones_away(self):
        """T73: the folders setup (and `systemctl --user enable` for it) made are on a list in setup's own folder; the
        dry run and the census name them and systemd's two links; uninstall takes each away when it is empty, and
        none at all when the list cannot be read"""
        listing = self.empty_home()
        before = listing()
        ud = os.path.join(self.home, ".config", "systemd", "user")
        links = [os.path.join(ud, "default.target.wants", "windstorm-tardigrade-revive.service"),
                 os.path.join(ud, "timers.target.wants", "windstorm-tardigrade-record.timer")]
        made = [".config", ".config/systemd", ".config/systemd/user", ".config/systemd/user/default.target.wants",
                ".config/systemd/user/timers.target.wants", ".config/windstorm-tardigrade", ".local", ".local/share",
                ".local/share/windstorm-tardigrade", ".local/state", ".local/state/windstorm-tardigrade"]
        rc, out = self.tg("setup", "--dry-run")
        self.assertEqual(rc, 0, out)
        for x in links + [os.path.join(self.home, m) for m in made]:
            self.assertIn(x, out)
        self.assertEqual(listing(), before, out)
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        lst = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "folders-made.json")
        self.assertEqual(jload(lst)["made"], made)
        self.assertEqual(os.stat(lst).st_mode & 0o777, 0o600)
        for x in links:
            self.assertTrue(os.path.islink(x), x)
        rc, out = self.tg("census")
        line = [ln for ln in out.splitlines() if ln.startswith("Setup also put here: ")]
        self.assertEqual(len(line), 1, out)
        for x in links + [os.path.join(self.home, m) for m in made]:
            self.assertIn(x, line[0])
        self.assertEqual(self.tg("census", "--json")[0], 0)
        places = json.loads(self.tg("census", "--json")[1])["setup_places"]
        self.assertEqual((places["links"], places["folders_made"]), (links, [os.path.join(self.home, m) for m in made]))
        # a second setup (an upgrade) keeps the list: nothing is new to it, and nothing is forgotten
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jload(lst)["made"], made)
        # a folder with something of the person's in it stays; the empty ones go, deepest first
        with open(os.path.join(ud, "my-own.service"), "w") as f:
            f.write("[Unit]\nDescription=my own\n")
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        after = listing()
        for gone in (".local/share", ".local/share/windstorm-tardigrade", ".config/systemd/user/default.target.wants",
                     ".config/systemd/user/timers.target.wants"):
            self.assertNotIn(gone, after, out)
        for stays in (".config/systemd/user/my-own.service", ".config/systemd/user", ".config/systemd", ".config",
                      ".config/windstorm-tardigrade", ".local/state/windstorm-tardigrade", ".local"):
            self.assertIn(stays, after, out)
        self.assertIn("Removed, being empty now, these folders that setup had made:", out)
        self.assertIn("  " + os.path.join(self.home, ".local", "share"), out)
        stay = [ln for ln in out.splitlines() if ln.startswith("Folders setup had made that stay, because they are not empty: ")]
        self.assertEqual(len(stay), 1, out)
        self.assertIn(ud, stay[0])
        # the list cannot be read: no folder is removed, and uninstall says so
        os.remove(os.path.join(ud, "my-own.service"))
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        with open(lst, "w") as f:
            f.write('{"written_by": "Windstorm Tardigrade setup", "made": ["../outside", ".local/share"]}\n')
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("No folder above them was removed: the list of the folders setup made", out)
        after = listing()
        self.assertNotIn(".local/share/windstorm-tardigrade", after, out)     # setup's own folder, known by its name
        for stays in (".local/share", ".config/systemd/user"):
            self.assertIn(stays, after, out)
        # a person's file of that name is not a list of ours: setup stops before it writes
        os.makedirs(os.path.dirname(lst))
        with open(lst, "w") as f:
            f.write('{"my": "own notes"}\n')
        rc, out = self.tg("setup")
        self.assertEqual(rc, 1, out)
        self.assertIn(lst, out)
        self.assertEqual(fread(lst), '{"my": "own notes"}\n')

    def test_G3_31_uninstall_leaves_the_home_as_setup_found_it_and_names_every_difference(self):
        """T82: the whole home is listed before setup and after uninstall; every difference is a path uninstall
        printed; it names only what is there, and says that its switch-off setting stays, where and why"""
        listing = self.empty_home()
        before = listing()
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        after = listing()
        self.assertEqual({k: v for k, v in after.items() if k in before}, before, out)
        cfgd = os.path.join(self.home, ".config", "windstorm-tardigrade")
        for rel in sorted(set(after) - set(before)):
            full = os.path.join(self.home, rel)
            named = full in out or (os.path.dirname(full) in (cfgd, self.state("").rstrip("/"))
                                    and any(ln.startswith(f"  also in {os.path.dirname(full)}: ")
                                            and os.path.basename(full) in ln for ln in out.splitlines()))
            self.assertTrue(named, f"{rel} is in the home after the uninstall and no line of it says so:\n{out}")
        for rel in (".local/share", ".config/systemd"):
            self.assertNotIn(rel, after, out)
        cfgf = os.path.join(cfgd, "config.json")
        self.assertEqual(jload(cfgf), {"auto_revive": False})
        self.assertIn(f"Automatic revive is switched off in your settings now (auto_revive false in {cfgf}). That line "
                      "stays: with it, a record made by hand does not wait for a boot revive that no longer runs.", out)
        # only what is there is named: with the record and the log gone, a run from a clone names neither
        os.remove(os.path.join(cfgd, "lanes.json"))
        os.remove(self.state("tardigrade.log"))
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("Tardigrade is already off this computer", out)
        self.assertIn(cfgf, out)
        for word in ("lanes.json", "tardigrade.log", "your record", "the log"):
            self.assertNotIn(word, out)
        self.assertEqual(listing(), {k: v for k, v in after.items()
                                     if k not in (".config/windstorm-tardigrade/lanes.json",
                                                  ".local/state/windstorm-tardigrade/tardigrade.log")}, out)
        # and in a home that never had it, an uninstall makes nothing and names nothing kept
        shutil.rmtree(os.path.join(self.home, ".config"))
        shutil.rmtree(os.path.join(self.home, ".local"))
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("Tardigrade is already off this computer", out)
        self.assertNotIn("Kept", out)
        self.assertEqual(listing(), before, out)

    def test_G5_12_uninstall_reads_each_answer_waits_for_a_revive_and_ends_with_what_is_gone_and_kept(self):
        """T59: gone is said only of what was read gone; what could not be removed or still runs is said with the
        step to take; a running revive is waited for; the installed copy alone runs it to its end, and a second
        run says it is already off"""
        self.stand_in_services()
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")
        ud = os.path.join(self.home, ".config", "systemd", "user")
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        # (a) a revive holds the lock: uninstall waits, says what holds it, and has removed nothing meanwhile
        holder = subprocess.Popen(["sleep", "60"])
        self.pids.append(self.remembered(holder.pid))
        os.makedirs(self.state("lock"))
        with open(self.state("lock/owner"), "w") as f:
            f.write(f"boot-a {holder.pid}\n")
        un = subprocess.Popen([sys.executable, "-I", SCRIPT, "uninstall"], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, env=self.env)
        self.pids.append(un.pid)
        time.sleep(2)
        self.assertIsNone(un.poll())
        self.assertTrue(os.path.exists(copy))
        self.assertTrue(os.path.exists(os.path.join(ud, "windstorm-tardigrade-revive.service")))
        os.remove(self.state("lock/owner"))
        os.rmdir(self.state("lock"))
        out = un.communicate(timeout=60)[0]
        self.assertEqual(un.returncode, 0, out)
        self.assertIn(f"Waiting: a revive is starting sessions right now (process {holder.pid})", out)
        self.assertIn("Nothing has been removed yet.", out)
        self.assertFalse(os.path.exists(copy), out)
        # its last lines: what is gone, then what is kept, with where
        self.assertIn("Removed:\n", out)
        for x in (copy, os.path.join(ud, "windstorm-tardigrade-revive.service"),
                  os.path.join(ud, "windstorm-tardigrade-record.timer")):
            self.assertIn("  " + x, out)
        for x in ("your settings: " + os.path.join(self.home, ".config", "windstorm-tardigrade", "config.json"),
                  "your record: " + os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json"),
                  "the log: " + self.state("tardigrade.log")):
            self.assertIn(x, out)
        self.assertLess(out.index("Removed:"), out.index("Kept, and what a setup after this does with each:"))
        # (b) a file of ours that cannot be removed: named with why, exit 1, and never "is off this computer"
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        if os.geteuid() != 0:
            os.chmod(ud, 0o500)
            self.addCleanup(lambda: os.path.isdir(ud) and os.chmod(ud, 0o700))
            rc, out = self.tg("uninstall")
            os.chmod(ud, 0o700)
            self.assertEqual(rc, 1, out)
            self.assertIn("These are Tardigrade's, but they could not be removed:", out)
            self.assertIn(os.path.join(ud, "windstorm-tardigrade-revive.service") + ": ", out)
            self.assertIn("Tardigrade is not all off this computer yet", out)
            self.assertNotIn("Tardigrade is off this computer", out)
            self.assertNotIn("  " + os.path.join(ud, "windstorm-tardigrade-revive.service") + "\n",
                             out[out.index("Removed:"):out.index("Kept")] if "Removed:" in out else "")
            rc, out = self.tg("setup")
            self.assertEqual(rc, 0, out)
        # (c) systemd says the timer runs again after the steps (it was read stopped before them). The tool stops it
        # once more itself, by name, and reads it; the command for the person is printed only where that did not
        # stop it, exit 1. What uninstall does when a unit is not read off BEFORE its steps is G5.27's.
        timer = "windstorm-tardigrade-record.timer"
        self.stand_in_services()
        self.manager(0, "4242")
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        os.remove(os.path.join(self.home, "systemctl.log"))
        self.plant(wakes_on_reload=[timer])
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Still on", out)
        self.assertNotIn("systemctl --user stop", out)
        self.assertIn("Tardigrade is off this computer", out)
        told = [x for x in fread(os.path.join(self.home, "systemctl.log")).splitlines() if not x.startswith("show ")]
        self.assertEqual(told[told.index("daemon-reload") + 1:], [f"stop {timer}"], told)
        self.plant()
        os.remove(os.path.join(self.bin, "systemd-active.json"))
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.plant(wakes_on_reload=[timer], deaf_after_reload=[timer])
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 1, out)
        self.assertIn("Still on: the record timer still runs (systemd says: active, waiting, 3 s after it was told to "
                      "stop). To stop it: systemctl --user stop windstorm-tardigrade-record.timer", out)
        self.assertNotIn("Tardigrade is off this computer", out)
        self.assertNotIn("timer and boot service are off", out)
        self.plant()
        # (d) systemd gives no answer after the steps: not said to be off, exit 3, with the next step
        os.remove(os.path.join(self.bin, "systemd-active.json"))
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.plant(no_bus_after_reload="Failed to connect to bus: No medium found")
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 3, out)
        self.assertIn("Could not be read, so it is not said to be off: systemctl gave no answer about the record timer "
                      '(exit 1: "Failed to connect to bus: No medium found"); a systemd user manager runs for this '
                      "account, and this shell cannot reach it", out)
        self.assertIn("systemctl --user stop windstorm-tardigrade-record.timer", out)
        self.assertNotIn("Tardigrade is off this computer", out)
        self.assertFalse(os.path.exists(copy), out)
        for name in ("default.target.wants/windstorm-tardigrade-revive.service",
                     "timers.target.wants/windstorm-tardigrade-record.timer"):
            self.assertFalse(os.path.lexists(os.path.join(ud, name)), out)
        self.plant()
        self.manager(1)
        # (e) the installed copy alone, with the plugin and every clone gone: to its end; then the same line again
        self.stand_in_services()
        os.remove(os.path.join(self.bin, "systemd-active.json"))
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        line = [sys.executable, "-I", copy, "uninstall"]
        r = subprocess.run(line, capture_output=True, text=True, env=self.env, timeout=120, cwd=self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # the numbered line for the person is the last thing before the stop (family rule 7), after the off line
        self.assertEqual(r.stdout.strip().splitlines()[-2:],
                         ["Tardigrade is off this computer: nothing comes back by itself any more. What stayed is "
                          "named above.",
                          "1. The line that ran this named the installed copy, and that copy is gone with the rest. If "
                          "the same line is run again, Python answers that it cannot open the file: that answer means "
                          "Tardigrade is off."])
        r = subprocess.run(line, capture_output=True, text=True, env=self.env, timeout=120, cwd=self.home)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("can't open file", r.stderr)
        # run twice from a script that is still there: safe, and it says so in its own words
        listing = self.whole_home()
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("Tardigrade is already off this computer: its boot service, its record timer and its installed "
                      "copy are not here, and nothing of it runs.", out)
        self.assertNotIn("Removed", out)
        self.assertEqual({k: v for k, v in self.whole_home().items() if k != "systemctl.log"},
                         {k: v for k, v in listing.items() if k != "systemctl.log"}, out)

    def test_G3_32_what_uninstall_and_the_census_say_of_the_kept_things_is_what_setup_then_does(self):
        """T92: after an uninstall its last lines and the census say what a setup does with the settings, the record
        and the log; the test then runs that setup and holds each sentence to it"""
        self.stand_in_services()
        self.start_lane("alpha", "Alpha Lane")
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        cfgf = os.path.join(self.home, ".config", "windstorm-tardigrade", "config.json")
        table = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        settings, log = jload(cfgf), fread(self.state("tardigrade.log"))
        self.assertTrue(settings["auto_revive"])
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        said = [f"  your settings: {cfgf}. A setup after this uses them as they are and switches automatic revive on again.",
                f"  your record: {table} (1 session(s)). A setup after this keeps it: the sessions still running then "
                "stay on it and are brought back after the next restart.",
                f"  the log: {self.state('tardigrade.log')}. A setup after this goes on writing it."]
        for x in said:
            self.assertIn(x, out)
        self.assertEqual(jload(cfgf), dict(settings, auto_revive=False))
        rc, out = self.tg("census")
        self.assertIn("Comes back after a reboot: NO (run setup)", out)
        self.assertIn("Tardigrade was taken off this computer (uninstall). Kept, and what a setup does with each:", out)
        for x in said:
            self.assertIn(x, out)
        self.assertEqual(json.loads(self.tg("census", "--json")[1])["comes_back"], "NO (run setup)")
        # the setup those lines speak of
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jload(cfgf), settings)                                 # as they were, automatic revive on again
        self.assertEqual([r["name"] for r in self.table()["lanes"]], ["Alpha Lane"])
        self.assertTrue(fread(self.state("tardigrade.log")).startswith(log))    # the same log, gone on
        self.assertNotIn("Tardigrade was taken off this computer", self.tg("census")[1])
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_BOOT_ID="boot-b")
        self.assertEqual(rc, 0, out)
        new = self.wait_launches(n + 1)[-1]
        self.assertIn("--resume", new["argv"])
        self.assertEqual(jload(self.state("last-revive.json"))["started"], ["Alpha Lane"], out)

    def test_G5_21_setup_after_an_uninstall_whole_or_cut_half_way_puts_everything_back_and_a_revive_works(self):
        """T91: setup decides by what is there now, never by what the kept settings or record remember: the circle
        setup, uninstall, setup, then a revive of a session that is down; and once more from an uninstall cut half
        way (the timer and the boot service off, the units gone, the copy and the setting still there)"""
        self.stand_in_services()
        sid = self.start_lane("alpha", "Alpha Lane")
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")
        ud = os.path.join(self.home, ".config", "systemd", "user")
        cfgf = os.path.join(self.home, ".config", "windstorm-tardigrade", "config.json")
        units = ("windstorm-tardigrade-revive.service", "windstorm-tardigrade-record.service",
                 "windstorm-tardigrade-record.timer")

        def whole():
            rc, out = self.tg("setup")
            self.assertEqual(rc, 0, out)
            self.assertIn("boot service: enabled   record timer: active", out)
            self.assertIn("Windstorm Tardigrade is set up.", out)
            self.assertEqual(fread(copy), fread(SCRIPT))
            for u in units:
                self.assertTrue(os.path.isfile(os.path.join(ud, u)), u)
            self.assertTrue(jload(cfgf)["auto_revive"])
            self.assertIn("Comes back after a reboot: yes, by itself", self.tg("census", TARDIGRADE_FAKE_LINGER="yes")[1])
            self.assertEqual([r["session_id"] for r in self.table()["lanes"]], [sid])

        def comes_back(boot):
            self.reboot()
            n = len(self.launches())
            rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_BOOT_ID=boot)
            self.assertEqual(rc, 0, out)
            a = self.wait_launches(n + 1)[-1]["argv"]
            self.assertEqual(a[a.index("--resume") + 1], sid)
            self.assertEqual(jload(self.state("last-revive.json"))["started"], ["Alpha Lane"], out)

        whole()
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("Comes back after a reboot: NO (run setup)", self.tg("census")[1])
        whole()                         # the kept settings and record did not decide it: what is there now did
        comes_back("boot-b")
        # an uninstall cut half way: its first steps done, then nothing more; the setting still says "on"
        env = dict(self.env, TARDIGRADE_FAKE_BOOT_ID="boot-b")
        for a in (["disable", "--now", units[2]], ["disable", units[0]]):
            subprocess.run([os.path.join(self.bin, "systemctl"), "--user", *a], env=env, check=True)
        for u in units:
            os.remove(os.path.join(ud, u))
        self.assertTrue(jload(cfgf)["auto_revive"])
        self.assertTrue(os.path.exists(copy))
        rc, out = self.tg("census", TARDIGRADE_FAKE_BOOT_ID="boot-b")
        self.assertIn("Comes back after a reboot: NO: ", out)
        rc, out = self.tg("setup", TARDIGRADE_FAKE_BOOT_ID="boot-b")
        self.assertEqual(rc, 0, out)
        self.assertIn("boot service: enabled   record timer: active", out)
        for u in units:
            self.assertTrue(os.path.isfile(os.path.join(ud, u)), u)
        self.assertIn("Comes back after a reboot: yes, by itself",
                      self.tg("census", TARDIGRADE_FAKE_BOOT_ID="boot-b", TARDIGRADE_FAKE_LINGER="yes")[1])
        comes_back("boot-c")

    def registry(self, rows, base=None):
        """write Claude Code's list of installed plugins as it keeps it; rows None writes a file that is not JSON"""
        d = os.path.join(base or os.path.join(self.home, ".claude"), "plugins")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "installed_plugins.json"), "w") as f:
            f.write("{ not json" if rows is None else json.dumps({"version": 2, "plugins": rows}))

    def test_G5_10_setup_from_an_older_script_stops_and_census_names_a_plugin_of_another_version(self):
        """T51: setup from a script older than the installed copy stops with both versions named and nothing
        changed, and goes on only with --downgrade; from the same or a newer script it says what was installed and
        what is now; census and the report name a plugin whose version differs, or could not be read"""
        import re
        self.stand_in_services()
        ver = re.search(r'^VERSION = "([0-9.]+)"', fread(SCRIPT), re.M).group(1)
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"Installed copy: version {ver} now; before this setup: none.", out)
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"Installed copy: version {ver} now; before this setup: version {ver} (the same).", out)

        def installed_says(v):
            text = fread(SCRIPT).replace(f'VERSION = "{ver}"', f'VERSION = "{v}"', 1)
            with open(copy, "w") as f:
                f.write(text)

        installed_says("99.0.1")                    # the machine holds a newer copy than this script
        before = self.whole_home()
        for a in (["setup"], ["setup", "--dry-run"]):
            rc, out = self.tg(*a)
            self.assertEqual(rc, 1, out)
            self.assertIn(f"NOT set up: the installed copy is version 99.0.1, and this script is the older version {ver}.", out)
            self.assertIn("Nothing was installed, and no file or folder was made.", out)
            self.assertIn("setup --downgrade. That word is the person's choice: an agent does not give it for them "
                          "unasked.", out)
            self.assertEqual({k: v for k, v in self.whole_home().items() if k != "systemctl.log"},
                             {k: v for k, v in before.items() if k != "systemctl.log"}, out)
        rc, out = self.tg("setup", "--downgrade")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"Installed copy: version {ver} now; before this setup: version 99.0.1.", out)
        self.assertEqual(fread(copy), fread(SCRIPT))
        installed_says("0.0.1")                     # an older copy: a newer script goes on and says both
        rc, out = self.tg("setup", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"this script is version {ver}; installed now: version 0.0.1", out)
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"Installed copy: version {ver} now; before this setup: version 0.0.1.", out)
        # the plugin Claude Code holds, beside the copy that runs at boot: the same, differs, could not be read
        key = "windstorm-tardigrade@some-marketplace"

        def census():
            rc, out = self.tg("census")
            return out, json.loads(self.tg("census", "--json")[1])["plugin"]

        out, j = census()                           # Claude Code keeps no list here: no plugin to speak of
        self.assertEqual(j["answer"], "none")
        self.assertNotIn("plugin", out)
        self.registry({key: [{"scope": "user", "version": ver}]})
        out, j = census()
        self.assertEqual((j["answer"], j["installed"], j["plugin"]), ("same", ver, ver))
        self.assertNotIn("plugin", out)
        self.registry({key: [{"scope": "user", "version": "0.0.9"}]})
        out, j = census()
        self.assertEqual((j["answer"], j["installed"], j["plugin"]), ("differs", ver, "0.0.9"))
        line = (f"The Claude Code plugin is version 0.0.9; the copy that runs at boot is version {ver}. Setup run from "
                "that plugin stops, so that it does not move this computer back: update the plugin in Claude Code.")
        self.assertIn(line, out)
        self.registry({key: [{"scope": "user", "version": "99.0.1"}]})
        out, j = census()
        self.assertEqual(j["answer"], "differs")
        self.assertIn(f"The Claude Code plugin is version 99.0.1; the copy that runs at boot is version {ver}. Run setup "
                      "again from the plugin to bring the copy up to it.", out)
        self.registry(None)
        out, j = census()
        self.assertEqual(j["answer"], "unread")
        self.assertIn("Claude Code's list of plugins could not be read (", out)
        self.assertIn("is not known. Nothing changes", out)
        self.registry({key: [{"scope": "user"}]})   # a row that names no version
        out, j = census()
        self.assertEqual(j["answer"], "unread")
        self.assertIn("could not be read, so whether they match is not known.", out)

    def test_G5_24_the_plugin_line_reads_every_row_claude_code_keeps_for_the_plugin(self):
        """T99: Claude Code keeps one key per catalog the plugin was installed from, and one row per scope under a
        key. Every row is read: an older, enabled plugin in a second row is named, in whichever order the file
        holds them, and "same" is said only when every row is the same"""
        import re
        self.stand_in_services()
        ver = re.search(r'^VERSION = "([0-9.]+)"', fread(SCRIPT), re.M).group(1)
        rc, out = self.tg("setup")
        self.assertEqual(rc, 0, out)
        own, shared = "windstorm-tardigrade@windstorm-tardigrade", "windstorm-tardigrade@a-shared-catalog"
        other = "another-plugin@a-shared-catalog"
        user = lambda v: {"scope": "user", "version": v, "installPath": "/nowhere/named/9.9.9"}

        def census(rows):
            self.registry(rows)
            out = self.tg("census")[1]
            j = json.loads(self.tg("census", "--json")[1])["plugin"]
            lines = [l for l in out.splitlines() if "plugin" in l]
            self.assertLessEqual(len(lines), 1, out)
            return (lines[0] if lines else ""), j

        step = (f"Next step: keep one. In a terminal, `claude plugin uninstall {shared}` or `claude plugin uninstall "
                f"{own}` takes that one off; which one is yours to say.")
        two = "That is more than one skill of one name: an agent may load any of them, and each runs its own script. "
        # two catalogs, the older row second: the case that was seen (the first row matched, and "same" was said)
        for order in ([shared, own], [own, shared]):
            rows = {other: [user("0.0.9")]}
            rows.update({k: [user(ver if k == shared else "0.0.9")] for k in order})
            line, j = census(rows)
            said = "; ".join(f"{k} at version {ver if k == shared else '0.0.9'}" for k in order)
            self.assertEqual(line, f"Claude Code holds the Tardigrade plugin 2 times: {said}. The copy that runs at boot "
                             f"is version {ver}. {two}Setup run from an older one stops, so that it does not move this "
                             f"computer back. {step}", order)
            self.assertEqual((j["answer"], j["installed"], j["plugin"]), ("differs", ver, "0.0.9"), order)
            self.assertEqual(j["rows"], [{"id": k, "scope": "user", "version": ver if k == shared else "0.0.9"}
                                         for k in order])
        # both the same as the copy: "same", and the two are still named once, with the step
        line, j = census({own: [user(ver)], shared: [user(ver)]})
        self.assertEqual(j["answer"], "same")
        self.assertEqual(line, f"Claude Code holds the Tardigrade plugin 2 times: {own} at version {ver}; {shared} at "
                         f"version {ver}. The copy that runs at boot is version {ver}. {two}{step}")
        # three rows: a second scope under one key, older; and a newer one beside it
        line, j = census({own: [user(ver), {"scope": "project", "version": "0.0.9"}], shared: [user("99.0.1")]})
        self.assertEqual(j["answer"], "differs")
        self.assertEqual(line, f"Claude Code holds the Tardigrade plugin 3 times: {own} (scope user) at version {ver}; "
                         f"{own} (scope project) at version 0.0.9; {shared} at version 99.0.1. The copy that runs at boot "
                         f"is version {ver}. {two}Setup run from an older one stops, so that it does not move this "
                         f"computer back. Setup run from a newer one brings the copy up to it. {step}")
        self.assertEqual(len(j["rows"]), 3)
        # one catalog, two scopes: named, and no word about two skills or taking one off
        line, j = census({own: [user(ver), {"scope": "project", "version": ver}]})
        self.assertEqual(j["answer"], "same")
        self.assertEqual(line, f"Claude Code holds the Tardigrade plugin 2 times: {own} (scope user) at version {ver}; "
                         f"{own} (scope project) at version {ver}. The copy that runs at boot is version {ver}.")
        # a row that cannot be read is said, never skipped: beside one that matches it is "unread", never "same"
        for bad in ([{"scope": "user"}], ["not a row"], "not a list"):
            line, j = census({shared: [user(ver)], own: bad})
            self.assertEqual(j["answer"], "unread", bad)
            self.assertIn(f"{own} at a version that could not be read", line, bad)
            line, j = census({own: bad, shared: [user("0.0.9")]})
            self.assertEqual((j["answer"], j["plugin"]), ("differs", "0.0.9"), bad)
        # one row alone, and none, are as before (G5.10); an emptied row is none
        line, j = census({shared: [user("0.0.9")], own: []})
        self.assertEqual((j["answer"], j["rows"]), ("differs", [{"id": shared, "scope": "user", "version": "0.0.9"}]))
        self.assertIn(f"The Claude Code plugin is version 0.0.9; the copy that runs at boot is version {ver}.", line)
        line, j = census({other: [user("0.0.9")], own: []})
        self.assertEqual((line, j["answer"], j["rows"]), ("", "none", []))

    def test_G3_19_a_copy_set_up_from_the_plugin_says_when_claude_code_removed_the_plugin_and_goes_on(self):
        """T60: the boot service and the timer go on until this tool's own uninstall. A copy set up from the
        plugin's folder says in one line, in the census, the report and the log at each boot, that Claude Code
        removed the plugin, that sessions still come back, and the command that takes it off; and the start goes
        ahead. No line with the row there, for a copy set up from a clone, or when the origin or the registry cannot
        be read"""
        import re
        self.stand_in_services()
        ver = re.search(r'^VERSION = "([0-9.]+)"', fread(SCRIPT), re.M).group(1)
        base = os.path.join(self.home, ".claude")
        plug = os.path.join(base, "plugins", "cache", "some-marketplace", "windstorm-tardigrade", ver, "scripts")
        os.makedirs(plug)
        shutil.copyfile(SCRIPT, os.path.join(plug, "tardigrade.py"))
        key = "windstorm-tardigrade@some-marketplace"
        notes = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "folders-made.json")
        said = ("Claude Code removed the Tardigrade plugin. What setup installed is still here: sessions are still "
                "brought back after a restart. To take it off: "
                "python3 -I ~/.local/share/windstorm-tardigrade/tardigrade.py uninstall")

        def from_plugin(*a, **over):
            r = subprocess.run([sys.executable, "-I", os.path.join(plug, "tardigrade.py"), *a], capture_output=True,
                               text=True, env=dict(self.env, **{k: str(v) for k, v in over.items()}), timeout=240)
            return r.returncode, r.stdout + r.stderr

        sid = self.start_lane("alpha", "Alpha Lane")
        self.registry({key: [{"scope": "user", "version": ver}]})
        rc, out = from_plugin("setup")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jload(notes)["from"], os.path.join(base, "plugins", "cache"))      # G5.25
        rc, out = self.tg("census")                                 # the row is there: no line
        self.assertNotIn("removed the Tardigrade plugin", out)
        self.assertEqual(json.loads(self.tg("census", "--json")[1])["plugin"]["answer"], "same")
        # Claude Code's own uninstall: the row emptied, or gone; the folder may stay
        for rows in ({key: []}, {}):
            self.registry(rows)
            rc, out = self.tg("census")
            self.assertIn(said, out)
            self.assertEqual(json.loads(self.tg("census", "--json")[1])["plugin"]["answer"], "removed")
            self.assertIn("Comes back after a reboot: yes", out)    # and it still does
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_BOOT_ID="boot-b")
        self.assertEqual(rc, 0, out)
        a = self.wait_launches(n + 1)[-1]["argv"]                   # the start goes ahead
        self.assertEqual(a[a.index("--resume") + 1], sid)
        self.assertIn(said, fread(self.state("tardigrade.log")))
        rc, out = self.tg("report", TARDIGRADE_FAKE_BOOT_ID="boot-b")
        self.assertIn(said, out)
        # the registry cannot be read, or is not there: never "removed", nothing changes, and it is said
        log_before = fread(self.state("tardigrade.log"))
        for make in (lambda: self.registry(None),
                     lambda: os.remove(os.path.join(base, "plugins", "installed_plugins.json"))):
            make()
            rc, out = self.tg("census", TARDIGRADE_FAKE_BOOT_ID="boot-b")
            self.assertNotIn("removed the Tardigrade plugin", out)
            self.assertIn("Claude Code's list of plugins could not be read (", out)
            self.assertIn("Comes back after a reboot: yes", out)
            self.assertEqual(json.loads(self.tg("census", "--json", TARDIGRADE_FAKE_BOOT_ID="boot-b")[1])["plugin"]["answer"],
                             "unread")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100, TARDIGRADE_FAKE_BOOT_ID="boot-c")
        self.assertEqual(rc, 0, out)
        self.wait_launches(n + 1)
        new_log = fread(self.state("tardigrade.log"))[len(log_before):]
        self.assertIn("Claude Code's list of plugins could not be read (", new_log)
        self.assertNotIn("removed the Tardigrade plugin", new_log)
        # where setup ran from cannot be read: with no row, no line
        self.registry({})
        text = fread(notes)
        with open(notes, "w") as f:
            f.write('{"written_by": "Windstorm Tardigrade setup", "made": [], "from": 7}\n')
        rc, out = self.tg("census", TARDIGRADE_FAKE_BOOT_ID="boot-c")
        self.assertNotIn("removed the Tardigrade plugin", out)
        os.remove(notes)
        self.assertNotIn("removed the Tardigrade plugin", self.tg("census", TARDIGRADE_FAKE_BOOT_ID="boot-c")[1])
        with open(notes, "w") as f:
            f.write(text)
        self.assertIn(said, self.tg("census", TARDIGRADE_FAKE_BOOT_ID="boot-c")[1])
        # a copy set up from a clone never had a row: a list with no row says nothing
        rc, out = self.tg("setup", TARDIGRADE_FAKE_BOOT_ID="boot-c")
        self.assertEqual(rc, 0, out)
        self.assertEqual(jload(notes)["from"], SCRIPT)
        rc, out = self.tg("census", TARDIGRADE_FAKE_BOOT_ID="boot-c")
        self.assertNotIn("removed the Tardigrade plugin", out)
        self.assertEqual(json.loads(self.tg("census", "--json", TARDIGRADE_FAKE_BOOT_ID="boot-c")[1])["plugin"]["answer"],
                         "none")
        # and setup run from the installed copy itself keeps what an earlier setup wrote down
        rc, out = from_plugin("setup", TARDIGRADE_FAKE_BOOT_ID="boot-c")
        self.assertEqual(rc, 0, out)
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")
        r = subprocess.run([sys.executable, "-I", copy, "setup"], capture_output=True, text=True,
                           env=dict(self.env, TARDIGRADE_FAKE_BOOT_ID="boot-c"), timeout=240)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(jload(notes)["from"], os.path.join(base, "plugins", "cache"))

    # ----- G5.25 (T100): nothing setup writes names a path inside a plugin cache version folder -----
    def plugin_copy(self):
        """a copy of the skill in a made-up plugin cache, as Claude Code lays one out:
        <home>/.claude/plugins/cache/<catalog>/<plugin>/<version>/scripts/tardigrade.py, with its row in Claude
        Code's list -> (the cache folder, the script in it, the row's key, the version)"""
        import re
        ver = re.search(r'^VERSION = "([0-9.]+)"', fread(SCRIPT), re.M).group(1)
        cache = os.path.join(self.home, ".claude", "plugins", "cache")
        plug = os.path.join(cache, "some-marketplace", "windstorm-tardigrade", ver, "scripts")
        os.makedirs(plug)
        shutil.copyfile(SCRIPT, os.path.join(plug, "tardigrade.py"))
        key = "windstorm-tardigrade@some-marketplace"
        self.registry({key: [{"scope": "user", "version": ver}]})
        return cache, os.path.join(plug, "tardigrade.py"), key, ver

    def below_cache(self, cache):
        """Everything in this test's home outside the made-up cache itself, read byte by byte: every file's whole
        content and every link's target. -> [(the path under home, the value found)] for each that names a path
        below the cache folder."""
        key, found = os.fsencode(cache + os.sep), []
        for d, dirs, files in os.walk(self.home):
            dirs[:] = [n for n in dirs if os.path.join(d, n) != cache or os.path.islink(os.path.join(d, n))]
            for n in sorted(dirs + files):
                p = os.path.join(d, n)
                if os.path.islink(p):
                    data = os.fsencode(os.readlink(p))
                elif os.path.isfile(p):
                    with open(p, "rb") as f:
                        data = f.read()
                else:
                    continue
                at = data.find(key)
                if at >= 0:
                    value = re.split(rb'[\s"<]', data[at:at + len(key) + 200])[0]
                    found.append((os.path.relpath(p, self.home), value.decode(errors="replace")))
        return found

    def test_G5_25_nothing_setup_writes_on_linux_names_a_path_inside_a_plugin_cache_version_folder(self):
        """T100, in the shape the family's rule gives: a copy of the skill in a made-up plugin cache version folder,
        setup run from there, then every file and every link setup left is read byte by byte for a path below the
        cache folder. Claude Code removes a plugin's older version folder after an update, so a stored path into
        one stops working. The list of made folders keeps the cache folder only; the plugin line answers the same
        from that and from a list an older version wrote; the next setup cuts an older list. Last, the reader is
        shown not to be blind: a unit pointed into the folder by hand is found."""
        self.stand_in_services()
        cache, script, key, ver = self.plugin_copy()
        notes = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "folders-made.json")
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")

        def answer():
            return json.loads(self.tg("census", "--json")[1])["plugin"]["answer"]

        def run(path, *a):
            r = subprocess.run([sys.executable, "-I", path, *a], capture_output=True, text=True, env=self.env,
                               timeout=240)
            return r.returncode, r.stdout + r.stderr

        self.start_lane("alpha", "Alpha Lane")
        rc, out = run(script, "setup")
        self.assertEqual(rc, 0, out)
        self.assertIn("is set up", out)
        ud = os.path.join(self.home, ".config", "systemd", "user")
        for unit in ("windstorm-tardigrade-revive.service", "windstorm-tardigrade-record.service",
                     "windstorm-tardigrade-record.timer"):
            self.assertTrue(os.path.isfile(os.path.join(ud, unit)), unit)       # what is read below is all there
        self.assertTrue(os.path.isfile(copy) and os.path.isfile(notes))
        self.assertEqual(self.below_cache(cache), [], "setup wrote a path below the plugin cache folder")
        self.assertEqual(self.updater_paths(), [], "setup wrote a path inside a folder another updater owns (G5.26)")
        self.assertEqual(jload(notes)["from"], cache)
        # the plugin line answers from the short value as it did from the whole path
        self.assertEqual(answer(), "same")
        self.registry({})
        self.assertEqual(answer(), "removed")
        # a list an older version wrote holds the script's whole path: read as before, with and without the row
        older = dict(jload(notes), **{"from": script})
        with open(notes, "w") as f:
            json.dump(older, f, indent=1)
        self.assertEqual(answer(), "removed")
        self.registry({key: [{"scope": "user", "version": ver}]})
        self.assertEqual(answer(), "same")
        self.assertEqual([p for p, _ in self.below_cache(cache)],
                         [os.path.relpath(notes, self.home)], "the reader finds the older list's value")
        # the next setup, run from the installed copy as the README says after an update, cuts the older list
        rc, out = run(copy, "setup")
        self.assertEqual(rc, 0, out)
        self.assertEqual((jload(notes)["from"], self.below_cache(cache), answer()), (cache, [], "same"))
        # a copy set up from a clone keeps the clone's script whole, as before
        rc, out = self.tg("setup")
        self.assertEqual((rc, jload(notes)["from"], self.below_cache(cache)), (0, SCRIPT, []), out)
        # the reader is not blind: a unit pointed into the version folder by hand is found, with its value
        unit = os.path.join(ud, "windstorm-tardigrade-revive.service")
        text = fread(unit)
        self.assertIn(copy, text)
        with open(unit, "w") as f:
            f.write(text.replace(copy, script))
        self.assertEqual(self.below_cache(cache), [(os.path.relpath(unit, self.home), script)])
        with open(unit, "w") as f:
            f.write(text)
        self.assertEqual(self.below_cache(cache), [])

    def test_G5_25_nothing_setup_writes_with_the_mac_stand_in_names_a_path_inside_a_plugin_cache_version_folder(self):
        """T100 on a Mac: the same read after setup run from the plugin copy with launchd's stand-in: the copy,
        the two launchd files and the list of made folders."""
        import contextlib
        import io
        from unittest import mock
        cache, script, key, ver = self.plugin_copy()
        tg = self.load_tool(script)
        patch = mock.patch.dict(os.environ, PATH=self.path)     # the tool's children find this test's launchctl
        patch.start()
        self.addCleanup(patch.stop)
        tg.MAC, tg.ARGS, tg.DRY = True, [], False
        tg.CFG["tmux"] = TMUX                   # the settings are read in main(), which a loaded tool does not run
        self.launchd()                                          # launchd takes both jobs and answers for both
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = tg.setup()
        la = os.path.join(self.home, "Library", "LaunchAgents")
        notes = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "folders-made.json")
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")
        for f in (copy, notes, *(os.path.join(la, f"ai.thewindstorm.tardigrade-{x}.plist") for x in ("revive", "record"))):
            self.assertTrue(os.path.isfile(f), f"{f}: {out.getvalue()}")      # what is read below is all there
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(self.below_cache(cache), [], "setup wrote a path below the plugin cache folder")
        self.assertEqual(self.updater_paths(), [], "setup wrote a path inside a folder another updater owns (G5.26)")
        self.assertEqual(jload(notes)["from"], cache)
        self.assertEqual(tg.plugin_facts()["answer"], "same")

    UPDATER_OWNED = re.compile(rb"Xcode[^/\s]*\.app/Contents/Developer|CommandLineTools/usr/bin|/Cellar/|/versions/[^/\s]+/bin/"
                               rb"|/installs/[^/\s]+/[^/\s]+/bin/|/pkgs/[^/\s]+/bin/")

    def updater_paths(self):
        """G5.26: in the folders setup writes into (its copy's, its state, its settings, the units, the launchd files),
        every file and link that names a path inside a folder an updater owns: Xcode, Apple's command line tools,
        Homebrew's Cellar, a pyenv, mise or asdf version folder, a conda package folder. -> [(path, what was found)]"""
        found = []
        with open(SCRIPT, "rb") as fh:
            tool = fh.read()
        for top in (os.path.join(self.home, ".local", "share", "windstorm-tardigrade"),
                    os.path.join(self.home, ".local", "state", "windstorm-tardigrade"),
                    os.path.join(self.home, ".config", "windstorm-tardigrade"),
                    os.path.join(self.home, ".config", "systemd"), os.path.join(self.home, "Library")):
            for d, dirs, files in os.walk(top):
                for n in sorted(dirs + files):
                    f = os.path.join(d, n)
                    if os.path.islink(f):
                        data = os.fsencode(os.readlink(f))
                    elif os.path.isfile(f):
                        with open(f, "rb") as fh:
                            data = fh.read()
                    else:
                        continue
                    if data == tool:                # the tool's own copy: its code names the marks it reads by
                        continue
                    m = self.UPDATER_OWNED.search(data)
                    if m:
                        found.append((os.path.relpath(f, self.home), m.group(0).decode()))
        return found

    def python_at(self, *parts):
        """a python3 at a made-up path under this test's folder: a link to the python that runs the suite"""
        d = os.path.join(self.tmp, *parts)
        os.makedirs(d, exist_ok=True)
        link = os.path.join(d, "python3")
        if not os.path.lexists(link):
            os.symlink(os.path.realpath(sys.executable), link)
        return link

    def setup_with(self, py, **env):
        r = subprocess.run([py, "-I", SCRIPT, "setup"], capture_output=True, text=True, env=dict(self.env, **env),
                           timeout=240)
        return r.returncode, r.stdout + r.stderr

    def unit_pythons(self):
        ud = os.path.join(self.home, ".config", "systemd", "user")
        return [re.search(r"^ExecStart=(.+?) -I ", fread(os.path.join(ud, u)), re.M).group(1)
                for u in ("windstorm-tardigrade-revive.service", "windstorm-tardigrade-record.service")]

    def test_G5_26_the_units_name_the_python_as_it_was_run_and_the_census_reads_whether_it_is_still_there(self):
        """T101. The units name the python as the person ran it, not resolved further. No path lasts for ever, and a
        job whose program is gone cannot say so: the census reads from each unit the python it names, with three
        answers (there, gone, could not be read), and names the one step, which is taken as printed."""
        self.stand_in_services()
        self.start_lane("alpha", "Alpha Lane")
        link = self.python_at("tools", "bin")
        self.assertNotEqual(link, os.path.realpath(link))
        rc, out = self.setup_with(link)
        self.assertEqual(rc, 0, out)
        self.assertIn("is set up", out)
        self.assertEqual(self.unit_pythons(), [link, link], "as it was run, not resolved")
        self.assertNotIn("The python written into", out)

        def programs():
            return [(os.path.basename(x["file"]), x["what"], x["program"], x["state"])
                    for x in json.loads(self.tg("census", "--json")[1])["stored_programs"]]

        def comes_back():
            return re.search(r"^Comes back after a reboot: (.*?)   Last record: ", self.tg("census")[1], re.M).group(1)

        there = [("windstorm-tardigrade-revive.service", "the boot service", link, "there"),
                 ("windstorm-tardigrade-record.service", "the record service", link, "there")]
        self.assertEqual((programs(), comes_back().split(",")[0]), (there, "yes"))
        os.remove(link)                                     # the folder it was in went with an update
        self.assertEqual(programs(), [x[:3] + ("gone",) for x in there])
        self.assertEqual(comes_back(), "NO: the python the boot service and the record service run with is gone: "
                         f"{link} (run setup)")
        again = self.python_at("tools-new", "bin")
        rc, out = self.setup_with(again)                    # the step, taken
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.unit_pythons(), comes_back().split(",")[0]), ([again] * 2, "yes"))
        if os.geteuid() != 0:                               # root reads through a closed folder
            closed = self.python_at("closed", "bin")
            rc, out = self.setup_with(closed)
            self.assertEqual((rc, self.unit_pythons()), (0, [closed, closed]), out)
            os.chmod(os.path.dirname(closed), 0)
            self.addCleanup(os.chmod, os.path.dirname(closed), 0o700)
            self.assertEqual([x[3] for x in programs()], ["unread", "unread"])
            self.assertEqual(comes_back(), "could not be read: the python the boot service and the record service run "
                             f"with, {closed} (Permission denied). Next: run setup again: it writes the python it is "
                             "run with")
            rc, out = self.setup_with(again)                # the step, taken
            self.assertEqual(rc, 0, out)
            self.assertEqual((self.unit_pythons(), comes_back().split(",")[0]), ([again] * 2, "yes"))
        self.assertEqual(self.updater_paths(), [])

    def test_G5_26_a_python_in_a_version_folder_is_not_written_and_setup_stops_where_it_has_no_other(self):
        """T101. Setup run by a python inside a pyenv, mise or asdf version folder or a conda package folder (an
        update of that tool takes the folder away) writes the system's python3 instead, where that is Python 3.9 or
        newer, and says so. Where there is none it stops before its first write, in a dry run too, and its step
        (the `python` setting, the person's own word) is taken as printed."""
        self.stand_in_services()
        system = self.python_at("usr", "bin")
        gone = os.path.join(self.tmp, "no-such", "python3")
        cfgf = os.path.join(self.home, ".config", "windstorm-tardigrade", "config.json")
        shapes = ((("h", ".pyenv", "versions", "3.12.1", "bin"), "a pyenv version folder"),
                  (("h", ".local", "share", "mise", "installs", "python", "3.12.1", "bin"), "a mise or asdf version folder"),
                  (("h", "miniconda3", "pkgs", "python-3.12.1-h0", "bin"), "a conda package folder"))
        py = self.python_at(*shapes[0][0])
        for dry in (True, False):                           # no other python: the stop, and nothing made
            r = subprocess.run([py, "-I", SCRIPT, "setup"] + (["--dry-run"] if dry else []), capture_output=True,
                               text=True, env=dict(self.env, TARDIGRADE_FAKE_SYSTEM_PYTHON=gone), timeout=240)
            self.assertEqual((r.returncode, r.stdout + r.stderr), (1, (
                f"NOT set up: the python that runs this setup, {py}, is in a pyenv version folder, which goes when it "
                "is updated or removed; the boot service would then stop without a word. Tardigrade found no other "
                "Python 3.9 or newer to name (/usr/bin/python3 is not there or is older). Nothing was installed, and "
                "no file or folder was made. Next step: put {\"python\": \"/whole/path/to/python3\"} into "
                f"{cfgf} (make the file if it is not there), naming a Python 3.9 or newer that stays where it is, then "
                "run setup again.\n")), dry)
            for d in (os.path.join(".local", "share", "windstorm-tardigrade"), os.path.join(".config", "systemd")):
                self.assertFalse(os.path.lexists(os.path.join(self.home, d)), f"nothing was made: {d}")
        stays = self.python_at("opt", "python", "bin")
        self.cfg({"python": stays})                         # the step, taken: the person's own word
        rc, out = self.setup_with(py, TARDIGRADE_FAKE_SYSTEM_PYTHON=gone)
        self.assertEqual((rc, self.unit_pythons()), (0, [stays, stays]), out)
        self.cfg({"python": gone})                          # a word that names no python stops too, with its step
        rc, out = self.setup_with(py, TARDIGRADE_FAKE_SYSTEM_PYTHON=system)
        self.assertEqual((rc, self.unit_pythons()), (1, [stays, stays]), out)
        self.assertIn(f"NOT set up: the `python` setting names {gone}, and that did not answer as a Python 3.9 or "
                      f"newer. Nothing was installed, and no file or folder was made. Next step: correct `python` in "
                      f"{cfgf} or take it out, then run setup again.", out)
        self.cfg({"python": ""})                            # that step, taken
        for parts, what in shapes:                          # with a system python: that one is written, and said
            py = self.python_at(*parts)
            rc, out = self.setup_with(py, TARDIGRADE_FAKE_SYSTEM_PYTHON=system)
            self.assertEqual((rc, self.unit_pythons()), (0, [system, system]), out)
            self.assertIn(f"The python written into the boot service and the record timer is {system}, not {py} that "
                          f"runs this setup: that one is in {what}, which goes when it is updated or removed.", out)
            self.assertEqual(self.updater_paths(), [], what)
            self.assertIn("Comes back after a reboot: yes", self.tg("census")[1])
        unit = os.path.join(self.home, ".config", "systemd", "user", "windstorm-tardigrade-revive.service")
        text = fread(unit)                                  # the reader is not blind: a unit pointed there is found
        with open(unit, "w") as f:
            f.write(text.replace(system, py))
        self.assertEqual(self.updater_paths(), [(os.path.relpath(unit, self.home), "/pkgs/python-3.12.1-h0/bin/")])

    def test_G5_26_with_the_mac_stand_in_apples_python_is_written_as_usr_bin_python3_and_homebrews_by_its_opt_folder(self):
        """T101 on a Mac, with launchd's stand-in. Python names the real program behind Apple's /usr/bin/python3,
        inside Xcode or Apple's command line tools: a launchd file that names that path stops without a word when
        Xcode is moved. What is written is /usr/bin/python3. A Homebrew python run by its Cellar path is written by
        its opt folder."""
        import contextlib
        import io
        from unittest import mock
        tg = self.load_tool()
        for p in (mock.patch.dict(os.environ, PATH=self.path),):    # the tool's children find this test's stand-ins
            p.start()
            self.addCleanup(p.stop)
        tg.MAC, tg.ARGS, tg.DRY = True, [], False
        tg.CFG["tmux"] = TMUX
        self.launchd()
        moved = os.path.join(self.tmp, "Developer-moved")
        self.script("xcode-select", f"#!/bin/sh\n[ \"$1\" = -p ] && echo {shlex.quote(moved)}\n")
        brew = os.path.join(self.tmp, "brew")
        cellar = self.python_at("brew", "Cellar", "python@3.12", "3.12.1", "bin")
        os.makedirs(os.path.join(brew, "opt", "python@3.12", "bin"))
        opt = os.path.join(brew, "opt", "python@3.12", "bin", "python3")
        os.symlink(cellar, opt)
        la = os.path.join(self.home, "Library", "LaunchAgents")
        stays = self.python_at("opt", "python", "bin")      # a python outside every such folder: written as run
        for ran, want in (("/Applications/Xcode.app/Contents/Developer/usr/bin/python3", "/usr/bin/python3"),
                          ("/Applications/Xcode-16.app/Contents/Developer/usr/bin/python3", "/usr/bin/python3"),
                          ("/Library/Developer/CommandLineTools/usr/bin/python3", "/usr/bin/python3"),
                          (os.path.join(moved, "usr", "bin", "python3"), "/usr/bin/python3"),
                          (cellar, opt), (stays, stays)):
            out = io.StringIO()
            with mock.patch.object(sys, "executable", ran), contextlib.redirect_stdout(out):
                rc = tg.setup()
            self.assertEqual(rc, 0, out.getvalue())
            got = [re.search(r"<string>(?:sleep \d+; )?exec (.+?) -I ", fread(os.path.join(
                la, f"ai.thewindstorm.tardigrade-{x}.plist"))).group(1) for x in ("revive", "record")]
            self.assertEqual(got, [want, want], ran)
            self.assertEqual(("The python written into the boot service and the record timer is "
                              f"{want}, not {ran} that runs this setup: " in out.getvalue()), ran != want, out.getvalue())
            self.assertEqual(self.updater_paths(), [], ran)
            self.assertEqual([(x["program"], x["state"]) for x in tg.stored_programs()],
                             [(want, "there" if os.path.exists(want) else "gone")] * 2, ran)

    def test_G5_27_on_a_mac_a_job_is_told_off_and_read_gone_before_it_is_loaded_again(self):
        """T107, family rule 30. Seen on a real Mac with another tool: `launchctl bootout` returns before a job whose
        process is still ending is gone, and a `bootstrap` sent at once answers code 5 and leaves the job not loaded.
        So setup run again while its own job runs reads the job gone before it loads it again; a job that does not
        go, and a read that gives no answer, each stop setup with what was read, and nothing is loaded over them."""
        tg, call = self.mac_tool()
        uid, log, la = os.getuid(), os.path.join(self.tmp, "launchctl.log"), os.path.join(self.home, "Library",
                                                                                          "LaunchAgents")
        self.launchd(linger=0.5)        # a job told off stays readable for half a second, as one whose process ends slowly
        for n in (1, 2, 3):
            rc, out = call(tg.setup)
            self.assertEqual(rc, 0, f"setup number {n}:\n{out}")
            self.assertEqual(sorted(self.launchd_holds()), sorted(self.JOBS), f"setup number {n}")
        calls = fread(log).splitlines()
        for label in self.JOBS:         # after each bootout of a loaded job: read as still there, then as gone, then loaded
            outs = [i for i, x in enumerate(calls) if x == f"bootout gui/{uid}/{label}"]
            self.assertEqual(len(outs), 3, calls)
            for i in outs[1:]:
                j = calls.index(f"bootstrap gui/{uid} {os.path.join(la, label + '.plist')}", i)
                self.assertGreaterEqual(calls[i:j].count(f"print gui/{uid}/{label}"), 2, calls[i:j + 1])
        # a job that never goes: said as that after the wait, and nothing is loaded over it
        self.launchd(never=(self.JOBS[0],))
        rc, out = call(tg.setup)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"NOT set up: {self.JOBS[0]} was not loaded again, because the one from before did not go: "
                      "launchd still holds it, 3 s after it was told to let it go. That job from before is still "
                      "loaded.\n", out)
        self.assertTrue(out.rstrip().endswith("Run setup again: it ends by reading what is loaded. To take them off, "
                                              "run uninstall."), out)
        self.assertNotIn("bootstrap", fread(log))
        self.assertNotIn("launchd took", out)
        # a read that gives no answer is neither gone nor there: exit 3, and that job is not loaded again
        self.launchd(unread={self.JOBS[1]: 37})
        rc, out = call(tg.setup)
        self.assertEqual(rc, 3, out)
        self.assertIn(f"Setup could not read whether launchd let go of {self.JOBS[1]}: launchctl gave no answer about "
                      f"{self.JOBS[1]} (exit 37). So it did not load that job again, and it does not say it is set "
                      "up.\n", out)
        self.assertIn(f"launchd took {self.JOBS[0]} before that, and it stays loaded.\n", out)
        self.assertEqual([x.split()[-1] for x in fread(log).splitlines() if x.startswith("bootstrap")],
                         [os.path.join(la, self.JOBS[0] + ".plist")])
        # and from there the way back in is the one the lines name
        self.launchd()
        rc, out = call(tg.setup)
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(self.launchd_holds()), sorted(self.JOBS))

    def test_G5_27_on_a_mac_uninstall_removes_nothing_until_both_jobs_are_read_gone(self):
        """T107, family rule 30: uninstall tells both jobs off and reads both gone before it removes anything. One
        that is not read gone: nothing is removed, each job is named with what was read, and the step is this
        file's own whole line. An account with no login at the screen has no job to tell off, and that is said."""
        tg, call = self.mac_tool()
        uid, log, la = os.getuid(), os.path.join(self.tmp, "launchctl.log"), os.path.join(self.home, "Library",
                                                                                          "LaunchAgents")
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")
        own = f"{shlex.quote(sys.executable)} -I {shlex.quote(os.path.abspath(SCRIPT))} uninstall"
        head = ("Tardigrade is NOT off this computer, and nothing was removed: uninstall takes nothing away before it "
                "has read every job of Tardigrade's off. What it read of each:\n")
        head3 = ("Uninstall could not read whether everything of Tardigrade's is off, and removed nothing: it takes "
                 "nothing away before it has read every job of Tardigrade's off. What it read of each:\n")
        stays = ("Whatever of Tardigrade's is installed here stays as it is, and macOS loads it again at your next "
                 "login at this Mac's screen, until uninstall has run to its end.\n")
        record_off = ("The record job was taken off by this run: no record is taken until your next login at this "
                      "Mac's screen, and a restart before that brings sessions back from the record as it is now. To "
                      f"have records taken again now, run setup again: {own[:-len('uninstall')]}setup\n")
        tail = "Running sessions were not touched.\n"
        second1 = ("What a second run can find different: a job whose process was still ending has gone, and the job is told off again with another 3 s to "
                   "take hold. If these same lines come back then, running it again changes nothing: what is STILL ON "
                   "has to go off first, and that is launchd's to do (a restart of this computer gives your account a "
                   "new launchd).\n")
        second3 = ("What a second run can find different: launchd answering. One that was busy or still starting answers "
                   "a little later. If these same lines come back then, running it again changes nothing: what stands "
                   "in the brackets above has to be put right first.\n")

        def home():
            return {k: v for k, v in self.whole_home().items() if not k.endswith("tardigrade.log")}
        self.launchd(linger=0.5)
        rc, out = call(tg.setup)
        self.assertEqual(rc, 0, out)
        before = home()
        # a job that never goes
        self.launchd(never=(self.JOBS[1],))
        rc, out = call(tg.uninstall)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out, head + f"  {self.JOBS[0]}: off, taken off by this run\n  {self.JOBS[1]}: STILL ON "
                         f"(launchd still holds it, 3 s after it was told to let it go)\n" + stays + tail
                         + second1 + f"Next step: run: {own}\n")
        self.assertEqual(home(), before)
        # the same with the first job not loaded when uninstall began: "off already", never "taken off by this run"
        self.launchd(never=(self.JOBS[1],), loaded=(self.JOBS[1],))
        rc, out = call(tg.uninstall)
        self.assertEqual(rc, 1, out)
        self.assertEqual(out, head + f"  {self.JOBS[0]}: off already\n  {self.JOBS[1]}: STILL ON (launchd still holds "
                         f"it, 3 s after it was told to let it go)\n" + stays + tail + second1 + f"Next step: run: {own}\n")
        # a read that gives no answer: exit 3 and its own first line (it is not known that anything is on); nothing
        # removed. This run took the record job off before it stopped: said, with the line that loads it again
        self.launchd(unread={self.JOBS[0]: 37}, loaded=self.JOBS)
        rc, out = call(tg.uninstall)
        self.assertEqual(rc, 3, out)
        self.assertEqual(out, head3 + f"  {self.JOBS[0]}: could not be read (launchctl gave no answer about "
                         f"{self.JOBS[0]} (exit 37))\n  {self.JOBS[1]}: off, taken off by this run\n" + stays
                         + record_off + tail + second3 + f"Next step: run uninstall again: {own}\n")
        self.assertNotIn("NOT off", out)
        self.assertEqual(home(), before)
        # a domain that answers anything but 125 is there: both jobs are told off, read until gone, and then it is off
        self.launchd(domain=1, linger=0.5, loaded=self.JOBS)
        rc, out = call(tg.uninstall)
        self.assertEqual(rc, 0, out)
        calls = fread(log).splitlines()
        for label in self.JOBS:
            self.assertIn(f"bootout gui/{uid}/{label}", calls)
            i = calls.index(f"bootout gui/{uid}/{label}")
            self.assertGreaterEqual(calls[i:].count(f"print gui/{uid}/{label}"), 2, calls)
        self.assertEqual(self.launchd_holds(), [])
        self.assertNotIn("Not asked of launchd", out)
        self.assertEqual(out.strip().splitlines()[-1], "Tardigrade is off this computer: nothing comes back by itself "
                         "any more. What stayed is named above.")
        for f in (copy, *(os.path.join(la, x + ".plist") for x in self.JOBS)):
            self.assertFalse(os.path.exists(f), f)
        # an account with no login at this Mac's screen: launchd holds no job for it; it can leave, and that is said
        self.launchd(loaded=())
        rc, out = call(tg.setup)
        self.assertEqual(rc, 0, out)
        self.launchd(domain=125, loaded=())
        rc, out = call(tg.uninstall)
        self.assertEqual(rc, 0, out)
        self.assertIn("Running sessions were not touched.\nNot asked of launchd: this account is not logged in at this "
                      "Mac's screen, so launchd holds no job for it.\n", out)
        self.assertNotIn("bootout", fread(log))
        self.assertEqual(out.strip().splitlines()[-1], "Tardigrade is off this computer: nothing comes back by itself "
                         "any more. What stayed is named above.")
        for f in (copy, *(os.path.join(la, x + ".plist") for x in self.JOBS)):
            self.assertFalse(os.path.exists(f), f)

    def test_G5_27_on_linux_uninstall_reads_every_unit_off_first_and_never_stops_the_boot_service(self):
        """T107, family rule 30. Seen with the released 0.1.10.1: uninstall run inside the boot service's wait said
        "off" while systemd still held that service. And seen on a throwaway service of the same shape: a stop of it
        can end the tmux server it started. So uninstall waits for a boot service that is on its way and never tells
        it to stop; it stops the timer and the record service and reads them stopped; and where a unit is not read
        off it removes nothing and says what it read of each, with its own whole line as the step."""
        T, S, R = ("windstorm-tardigrade-record.timer", "windstorm-tardigrade-record.service",
                   "windstorm-tardigrade-revive.service")
        ud = os.path.join(self.home, ".config", "systemd", "user")
        copy = os.path.join(self.home, ".local", "share", "windstorm-tardigrade", "tardigrade.py")
        log = os.path.join(self.home, "systemctl.log")
        own = f"{shlex.quote(sys.executable)} -I {shlex.quote(os.path.abspath(SCRIPT))} uninstall"
        head = ("Tardigrade is NOT off this computer, and nothing was removed: uninstall takes nothing away before it "
                "has read every unit of Tardigrade's off. What it read of each:\n")
        head3 = ("Uninstall could not read whether everything of Tardigrade's is off, and removed nothing: it takes "
                 "nothing away before it has read every unit of Tardigrade's off. What it read of each:\n")
        stays = ("Whatever of Tardigrade's is installed here stays as it is, and what is enabled starts again at the "
                 "next boot or login, until uninstall has run to its end.\n")
        record_off = ("The record timer was stopped by this run: no record is taken until the next boot or login, "
                      "and a restart before that brings sessions back from the record as it is now. To have records "
                      f"taken again now, run setup again: {own[:-len('uninstall')]}setup\n")
        tail = stays + "Running sessions were not touched.\n"
        second1 = ("What a second run can find different: a record run that was going has ended by itself, and the stop is sent again with another 3 s to "
                   "take hold. If these same lines come back then, running it again changes nothing: what is STILL ON "
                   "has to go off first, and that is systemd's to do (a restart of this computer gives your account a "
                   "new systemd).\n")
        second3 = ("What a second run can find different: systemd answering. One that was busy or still starting answers "
                   "a little later. If these same lines come back then, running it again changes nothing: what stands "
                   "in the brackets above has to be put right first.\n")
        off = "Tardigrade is off this computer: nothing comes back by itself any more. What stayed is named above."
        everything = []

        def home():
            return {k: v for k, v in self.whole_home().items()
                    if k != "systemctl.log" and not k.endswith("tardigrade.log")}

        def fresh(**plant):
            """a setup with a user manager running and its bus reachable; then the plant -> the home as it is then"""
            self.plant()
            self.manager(0, "4242")
            if os.path.exists(os.path.join(self.bin, "systemd-active.json")):
                os.remove(os.path.join(self.bin, "systemd-active.json"))
            rc, out = self.tg("setup")
            self.assertEqual(rc, 0, out)
            self.plant(**plant)
            os.remove(log)
            return home()

        def sent():
            """what systemd was told since the plant, its reads left out"""
            lines = fread(log).splitlines() if os.path.exists(log) else []
            everything.extend(lines)
            return [x for x in lines if not x.startswith("show ")]

        def gone():
            return not any(os.path.lexists(x) for x in (copy, *(os.path.join(ud, u) for u in (T, S, R))))
        self.stand_in_services()
        # (a) the boot service is on its way: uninstall waits, says so, tells it nothing, and goes on when it has ended
        fresh(states={R: ["activating", "start-pre"]}, flip={R: [4, "active", "exited"]})
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("Waiting:"), 1, out)
        self.assertRegex(out, r"^Waiting: the boot service is on its way \(systemd says: activating, start-pre\): its "
                         r"wait of \d+ s, then this boot's revive\. Uninstall never stops it, because the tmux server it "
                         r"starts lives in its group\. Nothing has been removed yet\. Uninstall goes on by itself when "
                         r"the service has ended; to stop waiting, press Ctrl-C and run uninstall again later\.\n")
        self.assertIn("Running sessions were not touched.\nsystemd still lists the boot service as ended (active, "
                      "exited), for as long as your account's systemd runs. It does nothing more. Uninstall did not "
                      "stop it, and you should not: the tmux server it started lives in its group, and a `systemctl "
                      "--user stop` of it can end that server and the sessions in it.\n", out)
        self.assertEqual(out.strip().splitlines()[-1], off)
        self.assertEqual(sent(), [f"stop {T}", f"disable {T}", f"disable {R}", "daemon-reload"])
        self.assertTrue(gone(), out)
        # (b) the person leaves that wait with Ctrl-C: said, nothing removed, nothing told to stop
        before = fresh(states={R: ["activating", "start-pre"]})
        run = subprocess.Popen([sys.executable, "-I", SCRIPT, "uninstall"], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, env=self.env)
        self.pids.append(run.pid)
        first = run.stdout.readline()
        self.assertTrue(first.startswith("Waiting: the boot service is on its way"), first)
        run.send_signal(signal.SIGINT)
        rest = run.communicate(timeout=60)[0]
        self.assertEqual(run.returncode, 130, rest)
        self.assertEqual(rest, f"\nStopped waiting. Nothing was removed and nothing was told to stop. Next step: run "
                         f"uninstall again later: {own}\n")
        self.assertEqual((home(), sent()), (before, []))
        # (c) this shell cannot reach the account's own systemd, and one runs: could not be read, nothing removed,
        #     and the step says where it can be done. systemd's words for it differ by version; both were seen
        for said in ("Failed to connect to bus: No medium found",
                     "Failed to connect to user scope bus via local transport: No such file or directory"):
            before = fresh(no_bus=said)
            rc, out = self.tg("uninstall")
            self.assertEqual(rc, 3, out)
            self.assertNotIn("NOT off", out)        # nothing was read: it is not known that anything of ours is on
            self.assertEqual(out, head3 + "".join(
                f'  {u}: could not be read (systemctl gave no answer about {what} (exit 1: "{said}"); a systemd user '
                "manager runs for this account, and this shell cannot reach it)\n"
                for u, what in ((T, "the record timer"), (S, "the record service"), (R, "the boot service")))
                + tail + "Next step: this shell cannot reach your account's own systemd (it has no user bus), so run "
                "uninstall again from a login of your own to this computer, such as ssh or a terminal at the screen: "
                f"{own}\n")
            self.assertEqual((home(), sent()), (before, []))
        # (d) and whether a user manager runs could not be read either: the same stop, with the plain step
        self.manager(2)
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 3, out)
        self.assertIn(f'  {T}: could not be read (systemctl gave no answer about the record timer (exit 1: "{said}"); '
                      "whether a systemd user manager runs for this account could not be read either)\n", out)
        self.assertTrue(out.endswith(tail + second3 + f"Next step: run uninstall again: {own}\n"), out)
        self.assertEqual((home(), sent()), (before, []))
        # (e) no user manager runs for this account at all: no unit of ours can run; it is off, and how that is
        #     known is said. What systemd could not be told (disable) uninstall does by its own hand, as before
        self.manager(1)
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("Running sessions were not touched.\nNot asked of systemd: no systemd user manager runs for this "
                      "account (the process list has no `systemd` of yours), so no unit of Tardigrade's runs.\n", out)
        self.assertEqual(out.strip().splitlines()[-1], off)
        self.assertTrue(gone(), out)
        for name in ("default.target.wants/" + R, "timers.target.wants/" + T):
            self.assertFalse(os.path.lexists(os.path.join(ud, name)), out)
        self.assertEqual(sent(), [f"disable {T}", f"disable {R}", "daemon-reload"])     # told; nothing answered
        # (f) an answer with a field missing is not an answer, with exit 0 too; an empty one neither
        for fields in (["LoadState", "ActiveState"], ["LoadState", "SubState"], ["KillMode"]):
            before = fresh(fields=fields)
            rc, out = self.tg("uninstall")
            self.assertEqual(rc, 3, (fields, out))
            self.assertEqual(out, head3 + "".join(
                f"  {u}: could not be read (systemctl's answer about {what} had no ActiveState or no SubState)\n"
                for u, what in ((T, "the record timer"), (S, "the record service"), (R, "the boot service")))
                + tail + second3 + f"Next step: run uninstall again: {own}\n", fields)
            self.assertEqual((home(), sent()), (before, []), fields)
        # (g) the timer does not stop: STILL ON after the wait, nothing removed; one stop was sent, to the timer
        before = fresh(deaf=[T])
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 1, out)
        self.assertEqual(out, head + f"  {T}: STILL ON (systemd says: active, waiting, 3 s after it was told to stop)\n"
                         f"  {S}: off already (systemd says: inactive)\n  {R}: off already (systemd says: inactive)\n"
                         + tail + second1 + f"Next step: run: {own}\n")
        self.assertEqual((home(), sent()), (before, [f"stop {T}"]))
        # (g2) the record service does not stop, AFTER this run stopped the timer: the list tells "stopped by this
        #      run" from "off already", and one sentence says no record is taken now, with the line that starts it again
        before = fresh(states={S: ["activating", "start"]}, deaf=[S])
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 1, out)
        self.assertEqual(out, head + f"  {T}: off, stopped by this run (systemd says: inactive)\n"
                         f"  {S}: STILL ON (systemd says: activating, start, 3 s after it was told to stop)\n"
                         f"  {R}: off already (systemd says: inactive)\n" + stays + record_off
                         + "Running sessions were not touched.\n" + second1 + f"Next step: run: {own}\n")
        self.assertEqual((home(), sent()), (before, [f"stop {T}", f"stop {S}"]))
        active = os.path.join(self.bin, "systemd-active.json")
        self.assertNotIn(T, jload(active))
        rc, again = self.tg("setup")            # the line that sentence names: the timer runs again
        self.assertIn(T, jload(active), again)
        # (g3) the same with the record service not read after the timer was stopped: exit 3, its own first line,
        #      and the same sentence
        before = fresh(unreadable=[S])
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 3, out)
        self.assertEqual(out, head3 + f"  {T}: off, stopped by this run (systemd says: inactive)\n"
                         f"  {S}: could not be read (systemctl's answer about the record service had no ActiveState or "
                         f"no SubState)\n  {R}: off already (systemd says: inactive)\n" + stays + record_off
                         + "Running sessions were not touched.\n" + second3 + f"Next step: run uninstall again: {own}\n")
        self.assertEqual((home(), sent()), (before, [f"stop {T}"]))
        # (h) each unit's file removed in turn under a running unit. The timer: stopped by name all the same (seen
        #     on systemd 255 with another tool's unit: a stop by name stops a unit whose file is gone), then read; its
        #     disable is its own call, which systemd refuses for a unit with no file, and its link goes by our hand
        fresh()
        os.remove(os.path.join(ud, T))
        rc, out = self.tg("uninstall")
        self.assertEqual((rc, out.strip().splitlines()[-1], gone()), (0, off, True), out)
        self.assertEqual(sent(), [f"stop {T}", f"disable {T}", f"disable {R}", "daemon-reload"])
        self.assertFalse(os.path.lexists(os.path.join(ud, "timers.target.wants", T)), out)
        # (h2) the record service in a run with its file gone: the same (its group holds the record run alone)
        fresh(states={S: ["activating", "start"]})
        os.remove(os.path.join(ud, S))
        rc, out = self.tg("uninstall")
        self.assertEqual((rc, out.strip().splitlines()[-1], gone()), (0, off, True), out)
        self.assertEqual(sent(), [f"stop {T}", f"stop {S}", f"disable {T}", f"disable {R}", "daemon-reload"])
        self.assertNotIn("systemctl --user stop", out)      # no systemctl line is handed to the person for them
        # (h3) the boot service has ended and its unit file is gone: read, counted as off and named; it is told
        #      nothing but its own disable, which systemd refuses for a unit with no file (its link goes by our hand)
        fresh(states={R: ["active", "exited"]})
        os.remove(os.path.join(ud, R))
        rc, out = self.tg("uninstall")
        self.assertEqual((rc, out.strip().splitlines()[-1], gone()), (0, off, True), out)
        self.assertIn("systemd still lists the boot service as ended (active, exited)", out)
        self.assertEqual(sent(), [f"stop {T}", f"disable {T}", f"disable {R}", "daemon-reload"])
        self.assertFalse(os.path.lexists(os.path.join(ud, "default.target.wants", R)), out)
        # (i) the record service is in a run: stopped and read stopped, after the timer; then everything goes
        fresh(states={S: ["activating", "start"]})
        rc, out = self.tg("uninstall")
        self.assertEqual((rc, out.strip().splitlines()[-1], gone()), (0, off, True), out)
        self.assertEqual(sent()[:2], [f"stop {T}", f"stop {S}"])
        # (j) a boot service systemd holds as failed (what 0.1.10.1 left behind): off; the mark is cleared after
        #     the units are gone, and that is the only thing it is ever told
        fresh(states={R: ["failed", "failed"]})
        rc, out = self.tg("uninstall")
        self.assertEqual((rc, out.strip().splitlines()[-1], gone()), (0, off, True), out)
        self.assertEqual(sent(), [f"stop {T}", f"disable {T}", f"disable {R}", "daemon-reload", f"reset-failed {R}"])
        self.assertIn("Running sessions were not touched.\nsystemd listed the boot service as failed from before; that "
                      "entry is cleared now.\n", out)
        self.assertRegex("\n".join(everything), rf"(?m)^reset-failed {re.escape(R)}\nshow {re.escape(R)} ")
        # (j2) "cleared" stands on a read after it: an entry that reset-failed leaves is said as still listed
        fresh(states={R: ["failed", "failed"]}, sticky_failed=[R])
        rc, out = self.tg("uninstall")
        self.assertEqual((rc, out.strip().splitlines()[-1], gone()), (0, off, True), out)
        self.assertIn("Running sessions were not touched.\nsystemd listed the boot service as failed from before. "
                      "Clearing that entry gave exit 0, and the read after it says it is still listed as failed. A "
                      "failed entry starts nothing.\n", out)
        self.assertNotIn("cleared now", out)
        sent()
        # no read asks systemd for a field the tool does not use
        self.assertEqual({x.split(" ", 2)[2] for x in everything if x.startswith("show ")},
                         {"-p ActiveState -p SubState"})
        # through all of it: no stop, restart or kill ever named the boot service, and disable never carried --now
        told = [x for x in everything if not x.startswith("show ")]
        self.assertEqual([x for x in told if re.match(r"(stop|restart|try-restart|kill)\b", x) and R in x], [], told)
        self.assertEqual([x for x in told if x.startswith("disable") and "--now" in x], [], told)
        self.assertNotIn(f"systemctl --user stop {R}", "\n".join(everything))

    def test_G5_27_the_next_step_names_the_python_that_is_running_the_tool(self):
        """T107: the step uninstall prints is its own whole line with the python that is running it, never the bare
        word python3. A person who ran another python because their python3 is older than the floor would get, with
        python3 in the step, a line that stops at the floor check."""
        self.stand_in_services()
        self.plant(no_bus="Failed to connect to bus: No medium found")
        self.manager(0, "4242")
        other = os.path.join(self.tmp, "a python of mine", "python3.99")
        os.makedirs(os.path.dirname(other))
        os.symlink(sys.executable, other)
        named = subprocess.run([other, "-I", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True,
                               env=self.env, timeout=60).stdout.strip()
        self.assertEqual(named, other)          # the python itself says it runs under that name
        r = subprocess.run([other, "-I", SCRIPT, "uninstall"], capture_output=True, text=True, env=self.env,
                           timeout=240)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertTrue(r.stdout.endswith("such as ssh or a terminal at the screen: "
                                          f"{shlex.quote(other)} -I {shlex.quote(os.path.abspath(SCRIPT))} uninstall\n"),
                        r.stdout)
        self.assertIn("'", shlex.quote(other))  # a path with a space in it comes quoted, so the line can be pasted
        self.assertNotIn("python3 -I", r.stdout)

    def test_G5_27_the_tool_has_no_stop_restart_or_kill_for_the_boot_service_in_its_code(self):
        """T107, family rule 30 (iv): the tool's own code is read for every stop, restart, kill and `--now` it can
        send. One `stop` in the whole file, in units_off, in the branch the boot service never takes; `--now` only
        where the timer is enabled; no restart and no kill at all. And no text of ours hands a person a stop of it."""
        src = fread(SCRIPT)
        tree = ast.parse(src)
        fn_of = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(fn):
                    fn_of.setdefault(id(n), fn.name)       # the outermost function: ast.walk goes outside in
        lists = [(fn_of.get(id(n), ""), [e.value for e in n.elts if isinstance(e, ast.Constant)], n)
                 for n in ast.walk(tree) if isinstance(n, ast.List) and n.elts
                 and isinstance(n.elts[0], ast.Constant) and n.elts[0].value in ("systemctl", "launchctl")]
        self.assertGreater(len(lists), 8, "the tool's calls are no longer written as lists: this test reads nothing")
        never = {"restart", "try-restart", "reload-or-restart", "kill", "kickstart", "isolate", "mask"}
        self.assertEqual([(f, w) for f, w, _ in lists if never & set(w)], [])
        stops = [(f, w, n) for f, w, n in lists if w[0] == "systemctl" and "stop" in w]
        self.assertEqual([(f, w) for f, w, _ in stops], [("units_off", ["systemctl", "--user", "stop"])])
        # that one stop names the loop's unit, and stands in the else of "if unit == BOOT_UNITS[0]"
        stop = stops[0][2]
        self.assertEqual(ast.unparse(stop.elts[-1]), "unit")
        units_off = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "units_off")
        guard = [n for n in ast.walk(units_off) if isinstance(n, ast.If) and ast.unparse(n.test) == "unit == BOOT_UNITS[0]"]
        self.assertEqual(len(guard), 1)
        in_body = any(x is stop for b in guard[0].body for x in ast.walk(b))
        in_else = any(x is stop for b in guard[0].orelse for x in ast.walk(b))
        self.assertEqual((in_body, in_else), (False, True))
        self.assertEqual(ast.literal_eval(re.search(r"^BOOT_UNITS = (\(.*\))$", src, re.M).group(1))[0],
                         "windstorm-tardigrade-revive.service")
        now = [(f, w) for f, w, _ in lists if "--now" in w]
        self.assertEqual(now, [("setup", ["systemctl", "--user", "enable", "--now", "windstorm-tardigrade-record.timer"])])
        # no line the tool prints, and no text of ours, hands a person a stop, restart or kill of the boot service
        hands = re.compile(r"systemctl --user (stop|restart|try-restart|kill)\b[^\n]*revive")
        self.assertEqual(hands.findall(src), [])
        for text in ("README.md", os.path.join("skills", "tardigrade", "SKILL.md")):
            for para in re.split(r"\n\s*\n", fread(os.path.join(ROOT, text))):
                flat = " ".join(para.split())
                if re.search(r"systemctl --user[^.]*\b(stop|restart|kill)\b", flat) and "revive" in flat:
                    self.assertRegex(flat, r"\b[Nn]ever\b", f"{text}: {flat[:200]}")

    def test_G5_6_the_record_keeps_labels_from_herdr_then_the_record_before_then_the_table(self):
        herdr = self.script("herdr", fread(FAKE_HERDR))
        for n in ("alpha", "beta", "gamma"):
            self.start_lane(n, n.title() + " Lane")
        self.tg("record", "--force", "--no-herdr")
        p = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        t = jload(p)
        ids = {r["tmux_session"]: r["session_id"] for r in t["lanes"]}
        for r in t["lanes"]:
            if r["tmux_session"] == "beta":
                r["herdr_tab"], r["herdr_workspace"] = "Beta Tab", "Team"        # on the record before
        jdump(t, p)
        table = os.path.join(self.home, "older-tool.tsv")                       # what another tool wrote
        with open(table, "w") as f:
            f.write("# a table\n" + "\t".join(["gamma", ids["gamma"], self.work, "", "", "", "Gamma Tab", "Old Team"]) + "\n")
        os.makedirs(os.path.join(self.home, ".config", "herdr"))
        with open(os.path.join(self.home, ".config", "herdr", "herdr.sock"), "w") as f:
            f.write("")
        jdump({"workspaces": [{"workspace_id": "w1", "label": "Team"}],
               "tabs": [{"tab_id": "t0", "label": "Alpha Tab", "workspace_id": "w1"},
                        {"tab_id": "t1", "label": "Beta New", "workspace_id": "w1"}],
               "panes": [{"pane_id": "p0", "tab_id": "t0", "workspace_id": "w1"},
                         {"pane_id": "p1", "tab_id": "t1", "workspace_id": "w1"}],
               "fail_panes": ["p1"],                                           # Herdr too slow for this one
               "process_info": {"p0": {"shell_pid": 1, "foreground_processes": [
                   {"pid": 2, "argv": [TMUX, "-S", self.sock, "attach", "-t", "=alpha"]}]}}, "visible": {}},
              os.path.join(self.home, "herdr-state.json"))
        self.cfg({"herdr": herdr, "herdr_tabs": True, "tsv_path": table})
        self.tg("record", "--force")
        got = {r["tmux_session"]: (r["herdr_tab"], r["herdr_workspace"]) for r in self.table()["lanes"]}
        self.assertEqual(got, {"alpha": ("Alpha Tab", "Team"), "beta": ("Beta Tab", "Team"),
                               "gamma": ("Gamma Tab", "Old Team")})
        src = fread(SCRIPT)
        setup = src[src.index("def setup():"):src.index("def mac_story(")]
        self.assertLess(setup.index('HERDR_WAIT["s"] = 120'), setup.index("record()"))   # setup gives Herdr longer

    def test_G5_8_the_boot_run_says_when_it_began_and_what_it_waited_for(self):
        self.cfg({"boot_wait_s": 15})
        self.assertIn("ExecStartPre=/bin/sleep 15", self.tg("setup", "--print-units")[1])     # its own wait, a setting
        self.cfg({"boot_wait_s": 45, "wake": "idle", "settle_s": 3, "starts_at_once": 1})
        for nm in ("alpha", "beta"):
            self.start_lane(nm, nm.title() + " Lane")
        self.tg("record", "--force")
        self.reboot()
        run = subprocess.Popen([sys.executable, "-I", SCRIPT, "revive", "--boot"],
                               env=dict(self.env, TARDIGRADE_FAKE_UPTIME_S="275", TARDIGRADE_FAKE_USER_UP_S="230"),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            end = time.time() + 20
            while time.time() < end and not os.path.exists(self.state("progress.json")):
                time.sleep(0.1)
            time.sleep(0.5)
            text = self.tg("census", TARDIGRADE_FAKE_UPTIME_S=280)[1]
            out, _ = run.communicate(timeout=120)
        finally:
            if run.poll() is None:
                run.kill()
        self.assertIn("boot run began 275 s after boot: this user's services came up 230 s after boot, then this "
                      "service's own wait of 45 s (boot_wait_s)", out)
        self.assertRegex(text, r"This boot's restore began .+ after boot: this user's services came up .+ after boot, "
                               r"then its own wait of 45 s")

    def test_G5_5_trust_on_and_off_write_the_setting_and_say_what_it_means(self):
        rc, out = self.tg("trust")
        self.assertIn("Trust answering is OFF", out)                # off unless switched on
        rc, out = self.tg("trust", "on")
        self.assertIn("answers Yes for you", out)
        self.assertTrue(jload(os.path.join(self.home, ".config", "windstorm-tardigrade", "config.json"))["answer_trust"])
        rc, out = self.tg("trust", "off")
        self.assertIn("Trust answering is OFF", out)
        self.assertFalse(jload(os.path.join(self.home, ".config", "windstorm-tardigrade", "config.json"))["answer_trust"])


class Windows(Base):
    def test_G4_1_open_prints_the_attach_line_when_it_cannot_open_a_window(self):
        self.start_lane("alpha", "Alpha Lane")
        rc, out = self.tg("open", "--dry-run")
        self.assertIn("Alpha Lane:", out)
        self.assertIn("attach -t '=alpha'", out)


    def test_G4_3_each_tab_is_attached_at_the_start_not_after_the_last_start(self):
        herdr = self.script("herdr", fread(FAKE_HERDR))
        for n in ("alpha", "beta", "gamma"):
            self.start_lane(n, n.title() + " Lane")
        self.tg("record", "--force")
        p = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        t = jload(p)
        for r in t["lanes"]:
            r["herdr_tab"], r["herdr_workspace"] = r["name"], "Team"
        jdump(t, p)
        self.reboot()
        os.makedirs(os.path.join(self.home, ".config", "herdr"))
        with open(os.path.join(self.home, ".config", "herdr", "herdr.sock"), "w") as f:
            f.write("")
        shell = [{"pid": 100, "name": "bash", "argv": ["bash"]}]
        jdump({"workspaces": [{"workspace_id": "w1", "label": "Team"}],
               "tabs": [{"tab_id": f"t{i}", "label": f"{n} Lane", "workspace_id": "w1"} for i, n in enumerate(("Alpha", "Beta", "Gamma"))],
               "panes": [{"pane_id": f"p{i}", "tab_id": f"t{i}", "workspace_id": "w1"} for i in range(3)],
               "process_info": {f"p{i}": {"shell_pid": 100, "foreground_processes": shell} for i in range(3)},
               "visible": {f"p{i}": "you@box:~$ \n" for i in range(3)}}, os.path.join(self.home, "herdr-state.json"))
        self.cfg({"herdr": herdr, "herdr_tabs": True, "wake": "idle", "starts_at_once": 1, "settle_s": 2})
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 3)
        runs = [json.loads(x) for x in fread(os.path.join(self.home, "herdr-runs.jsonl")).splitlines()]
        self.assertEqual(sorted(r["pane"] for r in runs if "cmd" in r), ["p0", "p1", "p2"], out)   # each once
        first_start = out.index("Alpha Lane: started")
        for nm in ("Alpha", "Beta", "Gamma"):
            # typed into its tab before the first session had even been started: to its waiting screen
            self.assertLess(out.index(f"herdr {nm} Lane: the attach was typed into its tab"), first_start, out)

    def herdr_missing_run(self, wait_min, on_done="", names=("alpha", "beta", "gamma")):
        """sessions with tabs on the record (three by default), a reboot, and no Herdr server; the boot revive runs
        in the background. Returns (the run, the file its output goes to)."""
        herdr = self.script("herdr", fread(FAKE_HERDR))
        for n in names:
            self.start_lane(n, n.title() + " Lane")
        self.tg("record", "--force")
        p = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        t = jload(p)
        for r in t["lanes"]:
            r["herdr_tab"], r["herdr_workspace"] = r["name"], "Team"
        jdump(t, p)
        self.reboot()
        os.makedirs(os.path.join(self.home, ".config", "herdr"), exist_ok=True)    # its folder, but no server
        shell = [{"pid": 100, "name": "bash", "argv": ["bash"]}]
        jdump({"workspaces": [{"workspace_id": "w1", "label": "Team"}],
               "tabs": [{"tab_id": f"t{i}", "label": f"{n.title()} Lane", "workspace_id": "w1"} for i, n in enumerate(names)],
               "panes": [{"pane_id": f"p{i}", "tab_id": f"t{i}", "workspace_id": "w1"} for i in range(len(names))],
               "process_info": {f"p{i}": {"shell_pid": 100, "foreground_processes": shell} for i in range(len(names))},
               "visible": {f"p{i}": "you@box:~$ \n" for i in range(len(names))}}, os.path.join(self.home, "herdr-state.json"))
        self.cfg({"herdr": herdr, "herdr_tabs": True, "wake": "idle", "settle_s": 2, "attach_wait_min": wait_min,
                  "on_done": on_done})
        log = os.path.join(self.home, "revive-out.txt")
        with open(log, "w") as f:
            run = subprocess.Popen([sys.executable, "-I", SCRIPT, "revive", "--boot"],
                                   env=dict(self.env, TARDIGRADE_FAKE_UPTIME_S="100"), stdout=f, stderr=subprocess.STDOUT)
        return run, log

    def until_said(self, log, words, run):
        end = time.time() + WAIT
        while time.time() < end:
            if words in fread(log):
                return fread(log)
            if run.poll() is not None:
                break
            time.sleep(0.2)
        self.fail(f"never said {words!r}: {fread(log)}")

    def test_G4_10_a_restore_says_at_once_when_herdr_is_not_running(self):
        # 1. Herdr comes up while the restore waits: said at the first pass, census says it, the tabs are attached
        run, log = self.herdr_missing_run(1)
        try:
            self.until_said(log, "Herdr is not running: 3 tab(s) wait for it", run)
            out = self.until_said(log, "revive: 3 started", run)
            first = out.index("Herdr is not running: 3 tab(s) wait for it (up to 1 minutes after the sessions have loaded)")
            self.assertLess(first, out.index("revive: 3 started"), out)          # at the first pass, not after the watch
            self.until_said(log, "count: started=3", run)
            text = self.tg("census", TARDIGRADE_FAKE_UPTIME_S=110)[1]
            self.assertIn("Herdr is not running: 3 tab(s) not attached; the restore attaches them when Herdr appears", text)
            with open(os.path.join(self.home, ".config", "herdr", "herdr.sock"), "w") as f:
                f.write("")                                                   # Herdr's server appears
            out = self.until_said(log, "attaching the tabs", run)
            run.wait(timeout=WAIT)
        finally:
            if run.poll() is None:
                run.kill()
        out = fread(log)
        self.assertEqual(out.count("Herdr is not running:"), 1, out)          # once, not once a pass
        self.assertNotIn("never came up", out)
        runs = [json.loads(x) for x in fread(os.path.join(self.home, "herdr-runs.jsonl")).splitlines()]
        self.assertEqual(sorted(r["pane"] for r in runs if "cmd" in r), ["p0", "p1", "p2"], out)
        self.assertNotIn("Herdr is not running", self.tg("census", TARDIGRADE_FAKE_UPTIME_S=200)[1])
        calls = [json.loads(x) for x in fread(os.path.join(self.home, "herdr-calls.jsonl")).splitlines()]
        self.assertFalse([c for c in calls if "server" in c], calls)         # Tardigrade never starts Herdr

    def test_G3_6_the_record_runs_again_once_every_start_is_done(self):
        # T37: the run waits for Herdr after every start is done; the record does not wait with it
        run, log = self.herdr_missing_run(1)
        try:
            self.until_said(log, "count: started=3", run)
            rc, out = self.tg("record")
            self.assertIn("record: 3 session(s)", out)
            self.assertNotIn("skipped", out)
            self.assertNotIn("do not start", self.tg("census", TARDIGRADE_FAKE_UPTIME_S=110)[1])
            self.assertTrue(jload(self.state("progress.json"))["starts_done"])
        finally:
            run.kill()
            run.wait(timeout=WAIT)
        src = fread(SCRIPT)
        rec = src[src.index("def record():"):src.index("mine, _, _ = live()", src.index("def record():"))]
        self.assertIn("run_going() and not starts_finished()", rec)            # still skipped while it starts them
        loop = src[src.index("waiting += len(inflight)"):src.index("end_due()", src.index("waiting += len(inflight)"))]
        self.assertIn('prog["starts_done"] = tries_done and not inflight', loop)

    def test_G3_6_a_session_that_dies_in_the_watch_is_written_as_it_would_be_at_any_other_time(self):
        # asked in review: the record now runs during the watch. A session that dies there alone is down, as it
        # would be at any other time; sessions that vanish together are a crash, kept as lost and brought back
        names = ("alpha", "beta", "gamma", "delta", "omega")
        n0 = len(self.launches())
        run, log = self.herdr_missing_run(1, names=names)
        try:
            self.until_said(log, "count: started=5", run)
            now = {x["sid"]: x["pid"] for x in self.launches()[n0:]}
            rows = {r["tmux_session"]: r["session_id"] for r in self.table()["lanes"]}
            def up(*nms):
                out = []
                for nm in nms:
                    try:
                        os.kill(int(now[rows[nm]]), 0)
                        out.append(nm)
                    except OSError:
                        pass
                return out
            self.kill(now[rows["alpha"]])                                     # one dies alone
            self.assertEqual(gone_soon(lambda: up("alpha"), within=10), [])
            self.assertIn("record: 4 session(s)", self.tg("record")[1])
            t = self.table()
            self.assertEqual(sorted(r["tmux_session"] for r in t["lanes"]), ["beta", "delta", "gamma", "omega"])
            self.assertEqual([r["tmux_session"] for r in t["down"] if r.get("down_since")], ["alpha"])
            for nm in ("beta", "gamma", "delta"):                             # then three of four vanish together
                self.kill(now[rows[nm]])
            self.assertEqual(gone_soon(lambda: up("beta", "gamma", "delta"), within=10), [])
            out = self.tg("record")[1]
            self.assertIn("vanished together (a crash?)", out)
            t = self.table()
            self.assertEqual(sorted(r["tmux_session"] for r in t["lanes"] if r.get("lost_since")), ["beta", "delta", "gamma"])
        finally:
            run.kill()
            run.wait(timeout=WAIT)
        socks = {r["tmux_session"]: r["tmux_socket"] for r in t["lanes"]}
        for nm in ("beta", "gamma", "delta"):           # their places are free again (a revive never takes one in use)
            self.tmux(socks[nm], "kill-session", "-t", "=" + nm)
        self.cfg({"attach_wait_min": 0.02})
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.wait_launches(n + 3)
        again = {x["sid"] for x in self.launches()[n:]}
        self.assertEqual(again, {rows[nm] for nm in ("beta", "gamma", "delta")}, out)   # the lost ones, not alpha

    def test_G3_17_a_session_that_goes_down_during_a_restore_stands_out_for_that_boot(self):
        n0 = len(self.launches())
        run, log = self.herdr_missing_run(1)
        try:
            self.until_said(log, "count: started=3", run)
            end = time.time() + WAIT                         # the run has taken its own look at the count
            while time.time() < end and not (jload(self.state("progress.json")) or {}).get("done"):
                time.sleep(0.2)
            now = {x["sid"]: x["pid"] for x in self.launches()[n0:]}
            rows = {r["tmux_session"]: r["session_id"] for r in self.table()["lanes"]}
            self.kill(now[rows["beta"]])                     # after the count, while the run waits for Herdr
            out = self.until_said(log, "Beta Lane: went down while the restore still waits (after the count)", run)
            self.assertEqual(out.count("went down while the restore still waits"), 1, out)     # said once
            text = self.tg("census", TARDIGRADE_FAKE_UPTIME_S=150)[1]
            self.assertRegex(text, r"Down during the restore: Beta Lane \(at \d\d:\d\d\): it started, then went down; "
                                   r"it may have failed to load")
            self.assertNotIn("Alpha Lane (at", text)
        finally:
            run.kill()
            run.wait(timeout=WAIT)
        rep = self.tg("report")[1]
        self.assertRegex(rep, r"down during the restore:\n +Beta Lane \(at \d\d:\d\d\)")
        d = json.loads(self.tg("census", "--json")[1])
        self.assertEqual([x["name"] for x in d["down_during_restore"]], ["Beta Lane"])
        # a day later, on another boot, it is an ordinary down row again: only this boot's restore is meant
        lr = jload(self.state("last-revive.json"))
        lr["boot"] = "another-boot"
        jdump(lr, self.state("last-revive.json"))
        self.assertNotIn("Down during the restore", self.tg("census")[1])

    def test_G4_10_when_herdr_never_comes_census_report_and_on_done_say_how_many_tabs_wait(self):
        told = os.path.join(self.home, "told.txt")
        run, log = self.herdr_missing_run(0.05, on_done=f"echo n={{herdr_tabs_not_attached}} e=$TARDIGRADE_HERDR_TABS_NOT_ATTACHED > {told}")
        try:
            run.wait(timeout=WAIT * 2)
        finally:
            if run.poll() is None:
                run.kill()
        out = fread(log)
        self.assertLess(out.index("Herdr is not running: 3 tab(s) wait for it"), out.index("herdr: its server never came up"), out)
        self.assertIn("; Herdr is not running: 3 tab(s) not attached", out)    # the on_done line
        self.assertEqual(fread(told).strip(), "n=3 e=3")
        text = self.tg("census", TARDIGRADE_FAKE_UPTIME_S=300)[1]
        self.assertIn("Herdr is not running: 3 tab(s) not attached. Tardigrade does not start Herdr", text)
        self.assertEqual(jload(self.state("herdr-wait.json"))["tabs"], 3)
        self.assertEqual(json.loads(self.tg("census", "--json")[1])["herdr_not_running"]["tabs"], 3)
        self.assertIn("Herdr was not running: 3 tab(s) not attached", self.tg("report")[1])
        # a machine without tabs on its record, or without Herdr set up, hears nothing of this
        os.remove(self.state("herdr-wait.json"))
        self.assertNotIn("Herdr is not running", self.tg("census")[1])

    def test_G4_11_census_says_herdr_is_not_running_only_from_herdrs_own_answer_at_that_moment(self):
        # T94: with the Herdr settings off, census took "Herdr is not running" from the file this boot's restore
        # wrote, and went on saying it after Herdr had started
        run, log = self.herdr_missing_run(0.05)
        try:
            run.wait(timeout=WAIT * 2)
        finally:
            if run.poll() is None:
                run.kill()
        since = jload(self.state("herdr-wait.json"))["since"]
        at = time.strftime("%H:%M", time.localtime(since))
        # asked, and Herdr answers nothing: its own answer now
        text = self.tg("census", TARDIGRADE_FAKE_UPTIME_S=300)[1]
        self.assertIn("Herdr is not running: 3 tab(s) not attached. Tardigrade does not start Herdr", text)
        self.assertTrue(json.loads(self.tg("census", "--json")[1])["herdr_not_running"]["asked"])
        # Herdr's server starts after the restore wrote its file
        with open(os.path.join(self.home, ".config", "herdr", "herdr.sock"), "w") as f:
            f.write("")
        prog = os.path.join(self.bin, "herdr")
        # the tool also looks for herdr in four system folders: on a machine that has one there, "no herdr program"
        # cannot be staged, and that case is left to the machines that have none (CI's have none)
        own = [d for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin") if os.access(os.path.join(d, "herdr"), os.X_OK)]
        cases = [{"herdr_tabs": False}] + ([] if own else [{"herdr_tabs": True, "herdr": None}])
        for off in cases:                               # the setting off: nothing is asked
            self.cfg(off)
            if off["herdr_tabs"]:
                os.rename(prog, prog + ".away")         # and no herdr program to be found: the same
            calls = os.path.join(self.home, "herdr-calls.jsonl")
            before = fread(calls) if os.path.exists(calls) else ""
            text = self.tg("census", TARDIGRADE_FAKE_UPTIME_S=300)[1]
            self.assertNotIn("Herdr is not running", text, off)
            self.assertIn(f"This boot's restore found Herdr not running at {at}: 3 tab(s) were not attached then. "
                          "This census did not ask Herdr", text, off)
            self.assertIn("so it does not say whether Herdr is running now.", text)
            self.assertEqual(fread(calls) if os.path.exists(calls) else "", before, "census asked Herdr with the setting off")
            d = json.loads(self.tg("census", "--json")[1])["herdr_not_running"]
            self.assertEqual((d["tabs"], d["since"], d["asked"]), (3, since, False))
        # asked again, and Herdr answers: no line at all
        if not own:
            os.rename(prog + ".away", prog)
        self.cfg({"herdr_tabs": True, "herdr": prog})
        text = self.tg("census", TARDIGRADE_FAKE_UPTIME_S=300)[1]
        self.assertNotIn("Herdr is not running", text)
        self.assertNotIn("found Herdr not running", text)
        self.assertEqual(json.loads(self.tg("census", "--json")[1])["herdr_not_running"], {})

    def lost_workspaces(self, places, extra_state):
        """sessions whose Herdr workspaces are gone after a reboot; returns (what herdr was asked, revive's log)"""
        herdr = self.script("herdr", fread(FAKE_HERDR))
        for n in places:
            self.start_lane(n, n.title() + " Lane")
        self.tg("record", "--force")
        p = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        t = jload(p)
        for r in t["lanes"]:
            r["herdr_tab"], r["herdr_workspace"] = r["name"], places[r["tmux_session"]]
        jdump(t, p)
        self.reboot()
        os.makedirs(os.path.join(self.home, ".config", "herdr"))
        with open(os.path.join(self.home, ".config", "herdr", "herdr.sock"), "w") as f:
            f.write("")
        jdump(dict({"workspaces": [{"workspace_id": "w1", "label": "Team"}], "tabs": [], "panes": [],
                    "process_info": {}, "visible": {}}, **extra_state), os.path.join(self.home, "herdr-state.json"))
        self.cfg({"herdr": herdr, "herdr_tabs": True, "wake": "idle", "herdr_default_workspace": "Team"})
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + len(places))
        return [json.loads(x) for x in fread(os.path.join(self.home, "herdr-runs.jsonl")).splitlines()], out

    SHELL = [{"pid": 100, "name": "bash", "argv": ["bash"]}]

    def test_G4_5_a_workspace_that_is_gone_is_made_again_under_its_old_name(self):
        runs, out = self.lost_workspaces({"alpha": "Gone", "beta": "Gone", "gamma": "Cannot Make"}, {
            "ws_fail": ["Cannot Make"], "process_info": {"wsp-Gone": {"shell_pid": 100, "foreground_processes": self.SHELL}},
            "visible": {"wsp-Gone": "you@box:~$ \n"}})
        made = [r for r in runs if "create_ws" in r]
        self.assertEqual(sorted(r["create_ws"] for r in made), ["Cannot Make", "Gone"], out)   # once per workspace
        self.assertTrue(all(r["no_focus"] for r in made))              # the person's view is never moved
        creates = [r for r in runs if "create" in r]
        self.assertIn({"create": "Beta Lane", "workspace": "wn-Gone", "no_focus": True}, creates)
        self.assertIn({"create": "Gamma Lane", "workspace": "w1", "no_focus": True}, creates)    # only then the default
        self.assertIn("workspace 'Gone' was gone; made again under its old name", out)
        self.assertIn("workspace 'Cannot Make' was gone; could NOT be made again", out)
        self.assertEqual(len([r for r in runs if "cmd" in r]), 3, out)  # each session got its tab

    def test_G4_7_the_tab_a_workspace_made_again_comes_with_is_used_not_left_blank(self):
        runs, out = self.lost_workspaces({"alpha": "Gone", "delta": "Busy Gone"}, {"process_info": {
            "wsp-Gone": {"shell_pid": 100, "foreground_processes": self.SHELL},
            "wsp-Busy Gone": {"shell_pid": 100, "foreground_processes": self.SHELL + [{"pid": 7, "argv": ["vim"]}]}},
            "visible": {"wsp-Gone": "you@box:~$ \n", "wsp-Busy Gone": "you@box:~$ vim\n"}})
        self.assertIn({"rename": "wst-Gone", "label": "Alpha Lane"}, runs)          # its first tab, renamed
        self.assertEqual([r["pane"] for r in runs if "cmd" in r and r["cmd"].endswith("'=alpha'")], ["wsp-Gone"], out)
        self.assertNotIn({"create": "Alpha Lane", "workspace": "wn-Gone", "no_focus": True}, runs)
        # a first tab that is not blank by then is left as it is, and a new tab is made
        self.assertNotIn("wsp-Busy Gone", [r.get("pane") for r in runs])
        self.assertNotIn("wst-Busy Gone", [r.get("rename") for r in runs])
        self.assertIn({"create": "Delta Lane", "workspace": "wn-Busy Gone", "no_focus": True}, runs)
        self.assertIn("the first tab of the workspace made again is not blank (it is running something else)", out)

    def test_G4_7_a_new_workspace_tab_whose_shell_is_still_starting_is_waited_for(self):
        # T34: a real Mac looked at the new workspace's first tab in the same second it was made, while its
        # shell was still starting, called it "not blank" and left it as a blank tab beside a new one
        runs, out = self.lost_workspaces({"alpha": "Gone"}, {"starting": {"wsp-Gone": 3}, "process_info": {
            "wsp-Gone": {"shell_pid": 100, "foreground_processes": self.SHELL}}, "visible": {"wsp-Gone": "you@box:~$ \n"}})
        self.assertIn({"rename": "wst-Gone", "label": "Alpha Lane"}, runs)           # its first tab is used
        self.assertEqual([r["pane"] for r in runs if "cmd" in r], ["wsp-Gone"], out)
        self.assertNotIn("Alpha Lane", [r.get("create") for r in runs])               # no second tab, no blank one
        self.assertNotIn("is not blank", out)
        self.assertGreaterEqual(int(fread(os.path.join(self.home, "herdr-asked-wsp-Gone"))), 4)

    def test_G4_9_after_a_restore_stopped_census_counts_its_waiting_screens_as_stopped(self):
        self.cfg({"starts_at_once": 1, "wake": "idle"})
        for nm in ("alpha", "beta", "gamma"):
            self.start_lane(nm, nm.title() + " Lane")
        self.tg("record", "--force")
        self.reboot()
        with open(os.path.join(self.home, "slow"), "w") as f:
            f.write("30")                                          # each start takes long, so the run is still going
        env = dict(self.env, TARDIGRADE_FAKE_UPTIME_S="100")
        run = subprocess.Popen([sys.executable, "-I", SCRIPT, "revive", "--boot"], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            end = time.time() + 20
            while time.time() < end and self.tmux(self.sock, "has-session", "-t", "=gamma")[0] != 0:
                time.sleep(0.1)
            d = json.loads(self.tg("census", "--json")[1])
            self.assertEqual(d["waiting_stopped"], 0)
            self.assertIn("Gamma Lane", [r["name"] for r in d["sessions"]["coming_back"]])
        finally:
            run.kill()                                             # the restore stops: its own process, by its pid
            run.wait()
        d = json.loads(self.tg("census", "--json")[1])
        self.assertEqual(d["coming_back"], 0)
        self.assertIn("Gamma Lane", [r["name"] for r in d["sessions"]["waiting_stopped"]])
        out = self.tg("census")[1]
        self.assertIn("WAITING, STOPPED: a waiting screen holds its place, but no restore is running", out)
        self.assertIn("Run `tardigrade revive`", out)

    def test_G4_8_revive_only_says_what_it_did_about_the_tab(self):
        herdr = self.script("herdr", fread(FAKE_HERDR))
        self.start_lane("alpha", "Alpha Lane")
        self.start_lane("beta", "Beta Lane")
        self.tg("record", "--force")
        p = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        t = jload(p)
        for r in t["lanes"]:
            r["herdr_tab"], r["herdr_workspace"] = r["name"], "Team"
        jdump(t, p)
        beta = [x for x in self.launches() if x["sid"] == t["lanes"][1]["session_id"]][0]
        self.kill(beta["pid"])
        time.sleep(1.5)
        os.makedirs(os.path.join(self.home, ".config", "herdr"))
        with open(os.path.join(self.home, ".config", "herdr", "herdr.sock"), "w") as f:
            f.write("")
        shell = [{"pid": 100, "name": "bash", "argv": ["bash"]}]
        jdump({"workspaces": [{"workspace_id": "w1", "label": "Team"}],
               "tabs": [{"tab_id": "t0", "label": "Alpha Lane", "workspace_id": "w1"},
                        {"tab_id": "t1", "label": "Beta Lane", "workspace_id": "w1"}],
               "panes": [{"pane_id": "p0", "tab_id": "t0", "workspace_id": "w1"}, {"pane_id": "p1", "tab_id": "t1", "workspace_id": "w1"}],
               "process_info": {"p0": {"shell_pid": 1, "foreground_processes": [
                                    {"pid": 2, "argv": [TMUX, "-S", self.sock, "attach", "-t", "=alpha"]}]},
                                "p1": {"shell_pid": 100, "foreground_processes": shell + [{"pid": 7, "argv": ["vim"]}]}},
               "visible": {}}, os.path.join(self.home, "herdr-state.json"))
        self.cfg({"herdr": herdr, "herdr_tabs": True, "wake": "idle"})
        rc, out = self.tg("revive", "--only", "Alpha Lane")                 # running, its tab already shows it
        self.assertIn("skip  Alpha Lane: already running", out)
        self.assertIn("revive --only Alpha Lane: tab: already there", out)
        rc, out = self.tg("revive", "--only", "Beta Lane")                  # brought back; its tab is busy
        self.assertIn("revive --only Beta Lane: tab: not attached: its tab is running something else; left alone", out)

    def test_G4_2_a_herdr_tab_gets_its_session_back_only_at_an_idle_shell_with_an_empty_prompt(self):
        herdr = self.script("herdr", fread(FAKE_HERDR))
        for n in ("alpha", "beta", "gamma", "delta"):
            self.start_lane(n, n.title() + " Lane")
        self.tg("record", "--force")
        p = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        t = jload(p)
        for r in t["lanes"]:
            r["herdr_tab"], r["herdr_workspace"] = r["name"], "Team"
        jdump(t, p)
        self.reboot()
        os.makedirs(os.path.join(self.home, ".config", "herdr"))
        with open(os.path.join(self.home, ".config", "herdr", "herdr.sock"), "w") as f:
            f.write("")
        shell = [{"pid": 100, "name": "bash", "argv": ["bash"]}]
        jdump({
            "workspaces": [{"workspace_id": "w1", "label": "Team"}],
            "tabs": [{"tab_id": f"t{i}", "label": f"{n} Lane", "workspace_id": "w1"} for i, n in enumerate(("Alpha", "Beta", "Gamma"))],
            "panes": [{"pane_id": f"p{i}", "tab_id": f"t{i}", "workspace_id": "w1"} for i in range(3)],
            "process_info": {"p0": {"shell_pid": 100, "foreground_processes": shell},
                             "p1": {"shell_pid": 100, "foreground_processes": shell},
                             "p2": {"shell_pid": 100, "foreground_processes": shell + [{"pid": 7, "name": "vim", "argv": ["vim"]}]}},
            "visible": {"p0": "you@box:~$ \n", "p1": "you@box:~$ git sta\n", "p2": ""},
        }, os.path.join(self.home, "herdr-state.json"))
        self.cfg({"herdr": herdr, "herdr_tabs": True, "wake": "idle"})
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 4)
        runs = [json.loads(x) for x in fread(os.path.join(self.home, "herdr-runs.jsonl")).splitlines()]
        typed = [r for r in runs if "cmd" in r]
        self.assertEqual(sorted(r["pane"] for r in typed), ["new-1", "p0"], out)      # Beta and Gamma: left alone
        for r in typed:
            self.assertRegex(r["cmd"], r"^env -u TMUX \S+ -S \S+ attach -t '=(alpha|delta)'$")   # quoted: zsh reads a bare =word as a command path   # only the attach, ever
        self.assertEqual([r for r in runs if "create" in r], [{"create": "Delta Lane", "workspace": "w1", "no_focus": True}])
        self.assertIn("Beta Lane: its tab has text typed after the prompt; left alone", out)
        self.assertIn("Gamma Lane: its tab is running something else; left alone", out)


@unittest.skipUnless(sys.platform.startswith("linux") and TMUX, "needs Linux and tmux")
class Names(Base):
    """G5.28 (T109). The world of every test in this file is LC_ALL=C with no TMUX: the one in which the real tmux
    prints "_" for a non-ASCII letter of a name AND for the tab between two fields to a client whose call carries
    no -u. Seen with the released 0.1.10.1 on real machines: the pane list came back with no line that could be
    split, every session read as "no terminal at all", and a record typed there moved every row to the tool's own
    socket under a made name."""

    FORMAT = "#{pane_pid}\t#{session_attached}\t#{session_name}"

    def plain_client(self, *a):
        """the real tmux asked with NO -u in this test's world: what the tool's own calls got before the cure"""
        r = subprocess.run([TMUX, "-S", self.sock, *a], capture_output=True, text=True, env=self.env, timeout=30)
        return r.returncode, r.stdout

    def pane_sid(self, session):
        """the conversation of the stand-in that runs in this tmux session's pane, read from the process tree"""
        rc, out = self.tmux(self.sock, "list-panes", "-t", f"={session}:", "-F", "#{pane_pid}")
        if rc != 0 or not out.split():
            return None
        top = int(out.split()[0])
        for row in reversed(self.launches()):
            p, steps = row["pid"], 0
            while p > 1 and steps < 6:
                if p == top:
                    return row["sid"]
                try:
                    p = int(fread(f"/proc/{p}/stat").rsplit(")", 1)[1].split()[1])
                except (OSError, ValueError, IndexError):
                    break
                steps += 1
        return None

    def places(self):
        return {r["session_id"]: (r["host"], r["tmux_socket"], r["tmux_session"]) for r in self.table()["lanes"]}

    def test_G5_28_a_session_is_found_and_its_name_read_whole_where_the_tool_is_started_with_LC_ALL_C(self):
        self.assertEqual(self.env.get("LC_ALL"), "C", "the world of this file")
        self.assertNotIn("TMUX", self.env)
        names = {"tést": "Accent Lane", "t_st": "Under Lane", "plain": "Plain Lane"}
        sid = {s: self.start_lane(s, n) for s, n in names.items()}
        # this world is the one with the fault: asked without -u, the real tmux gives the tool's own format back with
        # no tab in it and the two names alike. Were this ever not so, the test would hold nothing and says so here.
        rc, out = self.plain_client("list-panes", "-a", "-F", self.FORMAT)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("\t", out, "tmux printed the tab whole to a client with LC_ALL=C and no -u: not the world meant")
        self.assertNotIn("tést", out)
        self.assertEqual(sum(1 for x in out.splitlines() if x.endswith("_t_st")), 2, out)
        rc, out = self.tg("record", "--force")
        self.assertEqual(rc, 0, out)
        want = {sid[s]: ("tmux", self.sock, s) for s in names}
        self.assertEqual(self.places(), want, out)
        rc, out = self.tg("census")
        self.assertNotIn("no terminal at all", out)
        for s in names:
            self.assertIn(f"tmux default:{s} ", out)
        # the line printed for the person is theirs: the name whole, and no -u
        rc, out = self.tg("open", "--dry-run")
        for s, n in names.items():
            self.assertIn(f"{n}: {TMUX} -S {self.sock} attach -t '={s}'", out)
        self.assertNotIn(" -u ", out)
        # a restart: each comes back under its own name on its own socket, and each note goes into its own pane
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertEqual(rc, 0, out)
        self.wait_launches(n + 3)
        typed = self.wait_typed(3)
        self.assertEqual(sorted(t["sid"] for t in typed), sorted(sid.values()), f"one note each: {typed}\n{out}")
        for s in names:
            self.assertEqual(self.pane_sid(s), sid[s], f"the session in tmux session {s!r}:\n{out}")
        own = os.path.join(os.path.dirname(self.sock), "tardigrade")
        self.assertFalse(os.path.exists(own) and self.tmux(own, "list-sessions")[0] == 0,
                         "a session came back on the tool's own socket")
        self.assertEqual(len(self.launches()), n + 3, "no second copy")
        rc, out = self.tg("record", "--force")
        self.assertEqual(self.places(), want, out)

    def test_G5_28_every_tmux_call_of_the_tools_own_carries_u(self):
        """every call, whatever the path: a wrapper in front of the real tmux writes down the first argument of
        each call the tool makes through a record, a census, an open, a restart with its revive, a record and an
        uninstall (which reads every server for the sessions that carry the tool's mark)"""
        log = os.path.join(self.home, "tmux-first-arg.log")
        wrap = self.script("tmux-first-arg", '#!/bin/sh\nprintf "%s\\n" "$1" >> "$HOME/tmux-first-arg.log"\n'
                           f'exec {shlex.quote(TMUX)} "$@"\n')
        sid = self.start_lane("alpha", "Alpha Lane")
        self.cfg({"tmux": wrap})
        for verb in (("record", "--force"), ("census",), ("open", "--dry-run"), ("revive", "--dry-run", "--all")):
            rc, out = self.tg(*verb)
            self.assertEqual(rc, 0, f"{verb}: {out}")
        seen_reading = len(fread(log).split())
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertEqual(rc, 0, out)
        self.wait_launches(n + 1)
        self.assertEqual([t["sid"] for t in self.wait_typed(1)], [sid], out)
        self.tg("record", "--force")
        before_uninstall = len(fread(log).split())
        self.systemd_off()
        rc, out = self.tg("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertIn("1 tmux session that Tardigrade made still carries its mark", out)
        first = fread(log).split()
        self.assertGreater(seen_reading, 3, first)
        self.assertGreater(before_uninstall, seen_reading + 3, "the revive made tmux calls of its own")
        self.assertGreater(len(first), before_uninstall, "uninstall read the marks through tmux")
        self.assertEqual(set(first), {"-u"}, f"a tmux call of the tool's own went out without -u: {first}")

    def test_G5_28_a_pane_list_no_line_of_which_can_be_split_could_not_be_read_and_replaces_no_place(self):
        """family rule 6 on this read: tmux answered with lines and not one could be split at its tabs. That is the
        third answer, never "this session has no terminal": nothing a record holds is replaced on it, and no
        reason is logged that says a session has no pane. The answer is MADE here (a tmux in front of the real one
        answers the pane list of every server with one line as it was seen, "_" where the tabs were), so this test
        holds with or without the -u on the tool's calls."""
        sid = self.start_lane("alpha", "Alpha Lane")
        rc, out = self.tg("record", "--force")
        table = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        before = fread(table)
        self.assertEqual(self.places(), {sid: ("tmux", self.sock, "alpha")}, out)
        made = self.script("tmux-made-answer", '#!/bin/sh\nlp=""; all=""\n'
                           'for a; do [ "$a" = list-panes ] && lp=1; [ "$a" = -a ] && all=1; done\n'
                           '[ -n "$lp" ] && [ -n "$all" ] && { echo "4242_0_alpha"; exit 0; }\n'
                           f'exec {shlex.quote(TMUX)} "$@"\n')
        self.cfg({"tmux": made})
        why = f"the tmux server at {self.sock} listed 1 pane(s) in lines that could not be read (no tab between the fields)"
        for verb in (("record",), ("record", "--force")):
            time.sleep(1.1)                               # a record that wrote would change last_seen
            rc, out = self.tg(*verb)
            self.assertEqual(rc, 0, out)
            self.assertIn(f"record: skipped ({why}); the record is kept as it was", out, verb)
            self.assertEqual(fread(table), before, f"{verb}: the record changed")
        rc, out = self.tg("census")
        self.assertNotIn("no terminal at all", out)
        self.assertIn(why, out)
        rc, out = self.tg("revive", "--dry-run")
        self.assertNotIn(" start ", out)
        self.assertEqual(len(self.launches()), 1, "nothing was started")
        said = fread(self.state("tardigrade.log"))
        self.assertNotIn("no terminal", said)
        self.assertNotIn("its place on the record is now", said)
        # the limit: lines that DO split and none of them holds a session still mean it has no pane there
        self.cfg({"tmux": TMUX})
        loose = self.start_loose("Loose Lane")
        rc, out = self.tg("record", "--force")
        self.assertNotIn("skipped", out)
        got = self.places()
        self.assertEqual(got[sid], ("tmux", self.sock, "alpha"))
        self.assertNotEqual(got[loose][0], "tmux")
        self.assertEqual(got[loose][1], os.path.join(os.path.dirname(self.sock), "tardigrade"))

    def test_G5_28_a_row_the_version_before_moved_to_the_tools_own_socket_takes_its_true_place_at_the_next_record(self):
        """what 0.1.10.1 left where a record was typed with LC_ALL=C: the row of a session that runs in a tmux
        session of the person's, kept on the tool's own socket under a made name. The next record puts it back by its
        session id: one row, and one line in the log that says so."""
        sid = self.start_lane("tést", "Accent Lane")
        self.tg("record", "--force")
        table = os.path.join(self.home, ".config", "windstorm-tardigrade", "lanes.json")
        t = self.table()
        self.assertEqual(len(t["lanes"]), 1)
        own = os.path.join(os.path.dirname(self.sock), "tardigrade")
        t["lanes"][0].update(host="none", tmux_socket=own, tmux_session="accent-lane")
        jdump(t, table)
        said = ("record: Accent Lane: its place on the record is now tmux default:tést "
                "(the record had tardigrade:accent-lane)")
        rc, out = self.tg("record", "--force")
        self.assertEqual(rc, 0, out)
        self.assertIn(said, out)
        self.assertEqual(self.places(), {sid: ("tmux", self.sock, "tést")})
        self.assertEqual(self.table()["down"], [])
        self.tg("record", "--force")
        self.assertEqual(self.logged(said), 1, "said once, at the record that changed it")


class Tests(unittest.TestCase):
    def test_G7_1_a_run_stopped_part_way_leaves_nothing_behind(self):
        """a real run of one test, stopped by SIGTERM once its tmux server is up, as `timeout` stops it"""
        parent = tempfile.mkdtemp(prefix="tgstop")
        self.addCleanup(shutil.rmtree, parent, True)
        env = dict(os.environ, TARDIGRADE_TEST_TMP=parent)
        run = subprocess.Popen([sys.executable, "-m", "unittest",
                                "tests.test_tardigrade.Revive.test_G2_1_revive_brings_a_session_back_as_itself"],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: run.poll() is None and run.kill())
        end, up = time.time() + WAIT, False
        while time.time() < end and run.poll() is None and not up:
            up = any(os.path.exists(os.path.join(parent, d, f"tmux-{os.getuid()}", "default")) for d in os.listdir(parent))
            time.sleep(0.1)
        self.assertTrue(up, "the run never started its tmux server")
        time.sleep(0.5)
        run.send_signal(signal.SIGTERM)
        run.wait(timeout=30)

        def left():
            found = []
            for pid in os.listdir("/proc"):
                if pid.isdigit():
                    for part in ("cmdline", "environ"):       # a run in flight names the folder only in its HOME
                        try:
                            with open(f"/proc/{pid}/{part}", "rb") as f:
                                if parent.encode() in f.read():
                                    found.append(pid)
                                    break
                        except OSError:
                            pass
            return found
        def named(pids):
            # a failure names what it found, so a second one explains itself, in CI too: pid, parent, state, age,
            # command line (seen twice on a loaded machine: 2 and 3 processes still there after the wait)
            tick, rows = os.sysconf("SC_CLK_TCK"), []
            with open("/proc/uptime") as f:
                up = float(f.read().split()[0])
            for pid in pids:
                try:
                    with open(f"/proc/{pid}/stat") as f:
                        st = f.read().rsplit(")", 1)[1].split()
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
                    rows.append(f"pid {pid} parent {st[1]} state {st[0]} age {up - int(st[19]) / tick:.1f}s: {cmd[:200]}")
                except (OSError, IndexError, ValueError):
                    rows.append(f"pid {pid}: gone while being read")
            return "processes of the stopped run are still alive:\n" + "\n".join(rows)
        still = gone_soon(left)
        self.assertEqual(still, [], named(still))
        self.assertEqual(os.listdir(parent), [], "the stopped run left its folder behind")
        time.sleep(3)                                       # and nothing comes back to write in it later
        self.assertEqual(left(), [], "a process of the stopped run came back")
        self.assertEqual(os.listdir(parent), [], "something of the stopped run wrote in its folder afterwards")


    def test_G7_12_a_run_that_ends_early_or_writes_its_own_summary_is_not_green(self):
        """T108. Seen in a throwaway copy: a test that calls os._exit(0) ends the whole run with exit 0, before the
        runner's summary and before the gate, which runs in the same process; the CI step was that one command. So
        green is decided by tests/run_whole.py, outside the test process, and this test plants runs it must refuse.
        How the count is taken: the number of tests is read from the file's syntax (every test method of every
        TestCase class, inherited ones too) and held against the runner's "Ran N tests", which counts a skipped test
        as run; a run with a filter is not a deciding run, and run_whole takes none."""
        d = tempfile.mkdtemp(prefix="tgwhole")
        self.addCleanup(shutil.rmtree, d, True)
        run_whole = os.path.join(ROOT, "tests", "run_whole.py")
        sys.path.insert(0, os.path.dirname(run_whole))
        self.addCleanup(sys.path.remove, os.path.dirname(run_whole))
        import run_whole as rw

        def planted(body, more="", main="unittest.main(exit=False, verbosity=2)"):
            """a test file of three tests whose second does `body`, ending as this file ends: the gate's line last,
            then its own result"""
            f = os.path.join(d, f"planted_{len(os.listdir(d))}.py")
            with open(f, "w") as fh:
                fh.write("import json\nimport os\nimport sys\nimport unittest\n\n\n"
                         "class A(unittest.TestCase):\n    def test_1(self):\n        pass\n\n"
                         f"    def test_2(self):\n        {body}\n\n    def test_3(self):\n        pass\n{more}\n\n"
                         'if __name__ == "__main__":\n    res = os.environ.pop("TARDIGRADE_RUN_RESULT", None)\n'
                         f"    prog = {main}\n"
                         '    print("gate: 0 named test(s) passed in this run")\n    if res:\n'
                         '        with open(res, "w") as f:\n'
                         '            json.dump({"ran": prog.result.testsRun, "ok": prog.result.wasSuccessful()}, f)\n'
                         "    sys.exit(0 if prog.result.wasSuccessful() else 1)\n")
            return f

        def whole(*a):
            r = subprocess.run([sys.executable, "-I", "-B", run_whole, *a], capture_output=True, text=True, timeout=120)
            return r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1], r.stdout + r.stderr
        green = ("run_whole: GREEN: {0} tests defined and {0} run, exit 0, the summary, the gate's line and the run's "
                 "own result are there.")
        # the control: a run that passes is green; a test skipped on a condition of the machine counts as run
        self.assertEqual(whole(planted("pass"))[:2], (0, green.format(3)))
        self.assertEqual(whole(planted('self.skipTest("a real program is not here")'))[:2], (0, green.format(3)))
        self.assertEqual(whole(planted("pass", "\n\nclass B(A):\n    def test_4(self):\n        pass\n"))[:2],
                         (0, green.format(7)))                  # B runs A's three as well: counted as unittest does
        # a run that ends early with exit 0: no summary, no gate line, no result
        rc, last, out = whole(planted("os._exit(0)"))
        self.assertEqual((rc, last), (1, 'run_whole: NOT GREEN: the runner\'s summary line ("Ran N tests in ...") is not '
                                         'there; the runner\'s "OK" is not there; the gate\'s own line is not the last '
                                         "line of the run; the finished run wrote no result: it ended before its own "
                                         "end."), out)
        # the same run, having printed the summary, the OK and the gate's line itself: still no result of its own
        forged = ('sys.stderr.write("\\n" + "-" * 70 + "\\nRan 3 tests in 0.001s\\n\\nOK\\ngate: 0 named test(s) passed '
                  'in this run\\n"); sys.stderr.flush(); os._exit(0)')
        rc, last, out = whole(planted(forged))
        self.assertEqual((rc, last), (1, "run_whole: NOT GREEN: the finished run wrote no result: it ended before its "
                                         "own end."), out)
        # fewer tests run than the file defines (a filter inside the run): not green though every line is there
        rc, last, out = whole(planted("pass", main='unittest.main(exit=False, verbosity=2, argv=[sys.argv[0], "-k", '
                                                   '"test_1"])'))
        self.assertEqual((rc, last), (1, "run_whole: NOT GREEN: the runner says it ran 1 tests; the file defines 3; the "
                                         "run's own result counts 1 tests run; the file defines 3."), out)
        # a failing test, and a filter handed to run_whole itself
        rc, last, out = whole(planted('self.fail("planted")'))
        self.assertEqual(rc, 1, out)
        self.assertTrue(last.startswith("run_whole: NOT GREEN: the run ended with exit 1; "), last)
        self.assertEqual(whole(planted("pass"), "-k", "test_1")[:2],
                         (2, "run_whole: it takes one test file at most and no other word: a filtered run decides "
                             "nothing."))
        # this file: its tests counted from its syntax are the tests unittest collects from it
        here = os.path.abspath(__file__)
        self.assertEqual(rw.defined(here), unittest.TestLoader().loadTestsFromModule(     # a loader with no filter
            sys.modules[self.__class__.__module__]).countTestCases())
        # this file's own end: the result is the last thing a finished whole run writes, after the gate's line
        end = fread(here).rsplit('\nif __name__ == "__main__":\n', 1)[1]
        self.assertRegex(end, r'(?s)print\(f"gate: \{len\(gate_named\(\) & PASSED\)\} named test\(s\) passed in this '
                         r'run"\)\n    ok = prog\.result\.wasSuccessful\(\) and not bad\n    if res and whole:[^\n]*\n        with open\(res, "w"\) as f:\n'
                         r'            json\.dump\(\{"ran": prog\.result\.testsRun, "ok": ok\}, f\)\n'
                         r'    sys\.exit\(0 if ok else 1\)\n$')
        # the step that decides in CI is run_whole, and no step runs the test file by itself
        wf = fread(os.path.join(ROOT, ".github", "workflows", "tests.yml"))
        self.assertEqual(re.findall(r"(?m)^\s+- run: (python\S* .*)$", wf), ["python tests/run_whole.py"])
        self.assertNotIn("tests/test_tardigrade.py", wf)

    def test_G7_12_a_pass_that_a_test_prints_is_not_counted(self):
        """T108. A test can write a line shaped like the runner's own report for a test that no file defines. The
        gate counts outcomes from the runner's result class (addSuccess), never from report text: the printed name
        is in the report and not among what passed, and a row that names it is refused."""
        import io
        stream = io.StringIO()
        fake = "test_no_such_test (__main__.Nowhere.test_no_such_test) ... ok"

        def real(case):
            stream.write(fake + "\n")
        planted = type("PlantedPrinter", (unittest.TestCase,), {"test_the_real_one": real})
        result = unittest.TextTestRunner(stream=stream, resultclass=Kept, verbosity=2).run(
            unittest.TestLoader().loadTestsFromTestCase(planted))
        self.assertIn(fake, stream.getvalue())
        self.assertTrue(result.wasSuccessful())
        self.assertEqual((result.passed_names, result.skip_reasons), ({"test_the_real_one"}, {}))
        bad, here = runner_problems({"test_no_such_test"}, result.passed_names, result.skip_reasons)
        self.assertEqual((len(bad), here), (1, []), bad)
        self.assertIn("test_no_such_test: did not pass in this run", bad[0])

    def test_G7_2_a_check_that_a_process_is_gone_waits_for_its_exit_and_still_catches_one_that_stays(self):
        mark = f"tg-g72-{uuid.uuid4().hex[:8]}"
        def mine():
            found = []
            for pid in [x for x in os.listdir("/proc") if x.isdigit()]:
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        if mark.encode() in f.read():
                            found.append(pid)
                except OSError:
                    pass
            return found
        late = subprocess.Popen(["bash", "-c", f"sleep 1.5; : {mark}-late"])     # told to end; exits a moment later
        self.addCleanup(lambda: late.poll() is None and late.kill())
        self.assertNotEqual(mine(), [], "a single read sees it still there")
        t = time.time()
        self.assertEqual(gone_soon(mine), [])
        self.assertLess(time.time() - t, 5)
        late.wait()
        stays = subprocess.Popen(["bash", "-c", f"sleep 30; : {mark}-stays"])
        self.addCleanup(lambda: stays.poll() is None and stays.kill())
        t = time.time()
        self.assertEqual(gone_soon(mine, within=1.0), [str(stays.pid)], "one that stays is still caught")
        self.assertGreaterEqual(time.time() - t, 1.0)
        stays.kill()
        stays.wait()

    def test_G7_10_the_cleanup_of_a_stopped_run_ends_what_its_run_in_flight_starts_as_it_is_stopped(self):
        # T84: G7.1 failed under load (and once alone) on a session the tool's revive brought back while the run was
        # being stopped: its tmux server was started after the cleanup's one scan. Planted here without the tool: a
        # process of the test's (its folder only in its HOME, as a tardigrade run in flight) that, told to end, starts
        # one more process naming the folder and only then exits.
        tmp = tempfile.mkdtemp(prefix="tg")
        self.addCleanup(shutil.rmtree, tmp, True)
        late = os.path.join(tmp, "late")
        code = ("import os, signal, subprocess, sys, time\n"
                "def stop(*_):\n"
                "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', sys.argv[1]],\n"
                "                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "    time.sleep(0.3)\n"
                "    os._exit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                "open(sys.argv[2], 'w').close()\n"
                "while True:\n"
                "    time.sleep(1)\n")
        ready = os.path.join(tempfile.mkdtemp(prefix="g710"), "ready")
        self.addCleanup(shutil.rmtree, os.path.dirname(ready), True)
        run = subprocess.Popen([sys.executable, "-c", code, late, ready], env=dict(os.environ, HOME=os.path.join(tmp, "h")))
        self.addCleanup(lambda: run.poll() is None and run.kill())
        end = time.time() + 10
        while not os.path.exists(ready) and time.time() < end:
            time.sleep(0.05)
        test = Base("run")
        test.tmp = tmp
        find = test.processes_of_this_test
        self.assertEqual(find(), [run.pid])                       # found by its HOME alone
        stop = os.path.join(os.path.dirname(ready), "stop")
        try:
            self.assertEqual(test.end_mine(), [])
            self.assertEqual(gone_soon(find, within=1.0), [], "what it started as it was stopped is still there")
            run.wait(timeout=5)
            # the bound: a process that starts a new copy of itself each time it is told to end is still there when
            # the time is up; the cleanup then fails the test and names it, and never ends anything wider than a pid
            # Each copy starts with SIGTERM blocked and unblocks it once its handler is in: a SIGTERM sent while
            # Python is still starting waits for the handler instead of ending the chain early (CI on 11bd6c4 failed
            # so on all four jobs; a sleep of 0.5 s before the handler fails it here 3 of 3).
            chain = ("import os, signal, subprocess, sys, time\n"
                     "def masked():\n"
                     "    signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGTERM])\n"
                     "def again(*_):\n"
                     "    if not os.path.exists(sys.argv[2]):\n"
                     "        subprocess.Popen([sys.executable, '-c', sys.argv[3]] + sys.argv[1:], start_new_session=True,\n"
                     "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=masked)\n"
                     "    os._exit(0)\n"
                     "signal.signal(signal.SIGTERM, again)\n"
                     "signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGTERM])\n"
                     "while True:\n"
                     "    time.sleep(1)\n")
            first = subprocess.Popen([sys.executable, "-c", chain, os.path.join(tmp, "chain"), stop, chain],
                                     preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGTERM]))
            self.addCleanup(lambda: first.poll() is None and first.kill())
            gone_soon(lambda: [] if find() else ["none yet"], within=5.0)
            test.home, test.pids, test.loose, test.cleanup_bound = os.path.join(tmp, "h"), [], [], 1.5
            LIVE.append(test)
            t = time.time()
            with self.assertRaisesRegex(AssertionError, r"(?s)bound was reached.*pid \d+: \S*python\S* -c import os"):
                test.end_all()
            self.assertLess(time.time() - t, 10)
            self.assertNotIn(test, LIVE)
        finally:
            open(stop, "w").close()                                  # the chain ends at its next copy
            for _ in range(50):                                      # a failed run of this test leaves nothing either
                left = find()
                if not left:
                    break
                for pid in left:                                     # find() lists what names tmp, shown again here
                    signal_mine(pid, signal.SIGKILL, tmp, test=self._testMethodName)
                time.sleep(0.1)


class Signals(Base):
    def orphan(self, argv, env):
        """a process that is not this one's child (its starter has exited); ended at the end by its start time"""
        code = ("import subprocess, sys\n"
                "print(subprocess.Popen(sys.argv[1:], start_new_session=True, stdin=subprocess.DEVNULL,\n"
                "                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid)\n")
        out = subprocess.run([sys.executable, "-c", code] + argv, env=env, cwd="/", capture_output=True, text=True,
                             check=True)
        pid = int(out.stdout)
        started = start_time(pid)
        self.assertIsNotNone(started)
        self.assertNotEqual(parent_of(pid), os.getpid())
        self.addCleanup(signal_mine, pid, signal.SIGKILL, "", started)
        return pid, started

    def test_G7_11_the_suite_signals_a_remembered_pid_only_after_showing_it_is_still_the_tests(self):
        """T86: a pid is only a number. A signal goes out when the process is shown to be the test's still, by its
        folder, its remembered start time, or as the suite's own child; any other is not sent and fails the test"""
        me = self._testMethodName
        other = tempfile.mkdtemp(prefix="tgother")                 # a folder that is not this test's
        self.addCleanup(shutil.rmtree, other, True)
        clean = {"PATH": os.environ["PATH"], "HOME": other, "TMUX_TMPDIR": other}
        nap = [shutil.which("sleep"), "300"]
        gone = lambda pid: self.until(lambda: not running(pid), f"pid {pid} to go")
        asked = lambda: [x for x in NOT_SHOWN if x[0] == me]

        def forget():
            n = len(asked())
            NOT_SHOWN[:] = [x for x in NOT_SHOWN if x[0] != me]         # made on purpose: they do not fail this test
            return n
        # (1) a stranger: another program's process, as a reused number would be. Not signalled, written down
        stranger, born = self.orphan(nap, clean)
        self.assertEqual(shown_mine(stranger, self.tmp), "")
        self.assertFalse(self.kill(stranger))
        self.assertFalse(signal_mine(stranger, signal.SIGKILL, self.tmp, started=born + 1, test=me), "another start time")
        with self.assertRaisesRegex(GuardRefused, f"asked to signal pid {stranger} .*not shown to be the test's own"):
            os.kill(stranger, signal.SIGTERM)                          # past signal_mine: the hook refuses it
        with self.assertRaisesRegex(GuardRefused, "a whole process group"):
            os.killpg(os.getpgid(stranger), signal.SIGTERM)
        with self.assertRaisesRegex(GuardRefused, "a whole process group"):
            os.kill(-os.getpgid(stranger), signal.SIGTERM)
        os.kill(stranger, 0)                                           # asks only whether it is there: passes
        time.sleep(0.3)
        self.assertTrue(running(stranger), "a refused signal was sent")
        self.assertEqual((len(asked()), {x[1] for x in asked()} - {stranger, os.getpgid(stranger), -os.getpgid(stranger)}),
                         (5, set()))
        # the test that asked fails when it ends, whatever it asserts
        with self.assertRaisesRegex(GuardRefused, f"asked to signal pid {stranger}, not shown to be its own"):
            _watch_end(self)
        self.assertEqual(forget(), 0, "the end takes what it reports")
        # (1b) a process with nothing to read: no command line, and an environment that names nothing of ours. That
        # is how a process looks for a moment while it ends, so it is given three seconds to finish ending. One that
        # is alive and stays like that is a stranger like any other: waited for, never signalled, written down.
        hold = tempfile.mkdtemp(prefix="tgghost")
        self.addCleanup(shutil.rmtree, hold, True)
        fifo = os.path.join(hold, "f")
        os.mkfifo(fifo)
        blank = [shutil.which("bash"), "-c", 'exec 0<>"$1"; exec -a "" cat', "x", fifo]   # cat, named "", waits
        ghost, ghost_born = self.orphan(blank, clean)
        self.until(lambda: cmdline_of(ghost) == "", "the planted process to have no command line")
        self.assertEqual((start_time(ghost), shown_mine(ghost, self.tmp)), (ghost_born, ""))
        t0 = time.time()
        self.assertFalse(signal_mine(ghost, signal.SIGKILL, self.tmp, test=me))
        self.assertGreaterEqual(time.time() - t0, 2.9, "it was given the time to finish ending")
        time.sleep(0.3)
        self.assertTrue(running(ghost), "a live process with no command line was signalled")
        self.assertEqual([(x[1], x[2][:4]) for x in asked()], [(ghost, "'': ")], "and it is written down")
        self.assertEqual(forget(), 1)
        # one that does end during the wait was nobody's to signal: nothing sent, nothing written down
        threading.Timer(0.5, signal_mine, (ghost, signal.SIGKILL, "", ghost_born)).start()
        t0 = time.time()
        self.assertFalse(signal_mine(ghost, signal.SIGTERM, self.tmp, test=me))
        self.assertLess(time.time() - t0, 2.5, "the wait ends when the process has ended")
        gone(ghost)
        self.assertEqual(forget(), 0, "it ended while it was waited for: nothing to write down")
        # (2) shown by the start time remembered with it: sent
        self.assertTrue(signal_mine(stranger, signal.SIGTERM, self.tmp, started=born, test=me))
        gone(stranger)
        self.assertFalse(self.kill(stranger), "gone: no signal to a number")
        self.assertEqual(forget(), 0, "a process that is gone is nobody's: nothing to write down")
        # (3) shown by the test's folder: in its command line, or in its environment only
        a, _ = self.orphan([sys.executable, "-c", "import time; time.sleep(300)", os.path.join(self.tmp, "x")], clean)
        b, _ = self.orphan(nap, dict(clean, HOME=self.home))
        for pid in (a, b):
            self.assertEqual(shown_mine(pid, self.tmp), "the test's folder")
            self.assertTrue(self.kill(pid))
            gone(pid)
        # (4) the suite's own child: shown by the kernel's parent link; once it has ended there is nothing to signal
        child = subprocess.Popen(nap, env=clean, cwd="/")
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.assertEqual(shown_mine(child.pid, self.tmp), "this process's own child")
        os.kill(child.pid, signal.SIGTERM)                             # what Popen.kill and send_signal do
        self.until(lambda: not running(child.pid), "the child to end")
        self.assertIsNotNone(start_time(child.pid), "ended and not yet read: its number is still held")
        self.assertFalse(self.kill(child.pid))
        self.assertEqual(shown_mine(child.pid, self.tmp), "this process's own child", "held for us while it is not read")
        child.wait(timeout=5)
        # the other half: once its exit has been read the number is free again, and it is ours no more
        self.assertEqual(forget(), 0)
        self.assertEqual(shown_mine(child.pid, self.tmp), "")
        self.assertFalse(self.kill(child.pid))
        with self.assertRaisesRegex(GuardRefused, f"asked to signal pid {child.pid} "):
            os.kill(child.pid, signal.SIGTERM)
        self.assertEqual(forget(), 1)
        # (5) with the hook taken out the same signal is delivered: the hook is what refused it
        loose, born = self.orphan(nap, clean)
        res = planted(lambda: os.kill(loose, signal.SIGTERM), out=True)
        self.assertTrue(res.wasSuccessful() and res.testsRun == 1, res.failures + res.errors)
        gone(loose)
        self.assertEqual(forget(), 0)
        # (6) nothing in this file signals a pid any other way
        import ast
        tree, out = ast.parse(fread(__file__)), []
        inside = {}                                     # each call -> the named function it stands in
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef):
                for c in ast.walk(fn):
                    if isinstance(c, ast.Call):
                        inside[c] = fn.name             # the innermost wins: ast.walk reaches inner functions later
        for c, where in inside.items():
            if (isinstance(c.func, ast.Attribute) and c.func.attr in ("kill", "killpg")
                    and isinstance(c.func.value, ast.Name) and c.func.value.id == "os"):
                probe = len(c.args) == 2 and isinstance(c.args[1], ast.Constant) and c.args[1].value == 0
                if not probe and where not in ("signal_mine", "_on_signal", me, GATE_TEST):
                    out.append(f"line {c.lineno} in {where}")
        self.assertEqual(sorted(set(out)), [], "a signal sent past signal_mine")

    def test_G5_18_the_guard_catches_each_kind_in_a_planted_test_and_only_the_gate_can_take_it_out(self):
        """T87: the guard's parts 2 to 5. A planted test of each kind is caught, also when it swallows the error, and
        passes with the guard taken out; nothing but a planted case of this test can take it out; no setting sends
        the suite's folders into the real home"""
        probe = os.path.join(REAL_HOME, ".tardigrade-guard-probe")      # never there: each act fails before it makes it
        self.assertFalse(os.path.lexists(probe))
        good = {"HOME": self.home, "PATH": self.path, "TMUX_TMPDIR": self.tmp}
        true = shutil.which("true")
        other = tempfile.mkdtemp(prefix="tgother")
        self.addCleanup(shutil.rmtree, other, True)
        nap = [shutil.which("sleep"), "300"]
        away = {"PATH": os.environ["PATH"], "HOME": other, "TMUX_TMPDIR": other}

        def quiet(argv, env):
            return subprocess.run(argv, env=env, cwd="/", capture_output=True)

        def no(key):
            return {k: v for k, v in good.items() if k != key}
        kinds = [
            ("open in the home of the account", lambda: open(os.path.join(probe, "none"))),
            ("os.listdir in the home of the account", lambda: os.listdir(probe)),
            ("os.mkdir in the home of the account", lambda: os.mkdir(os.path.join(probe, "a", "b"))),
            ("os.rename in the home of the account",
             lambda: os.rename(os.path.join(self.tmp, "none"), os.path.join(probe, "x"))),
            ("shutil.copyfile in the home of the account",
             lambda: shutil.copyfile(os.path.join(probe, "none"), os.path.join(self.tmp, "copied"))),
            ("a child handed the real home", lambda: quiet([true], dict(good, HOME=REAL_HOME))),
            ("a child with no HOME", lambda: quiet([true], no("HOME"))),
            ("a child with no tmux folder of its own", lambda: quiet([true], no("TMUX_TMPDIR"))),
            ("a child whose PATH finds this machine's own tmux", lambda: quiet([true], dict(good, PATH="/usr/bin:/bin"))),
            ("a child whose PATH finds this machine's own", lambda: quiet([true], no("PATH"))),
            ("this machine's own tmux, by its path", lambda: quiet([_REAL_TMUX, "-V"], good)),
            ("a stand-in that fails loudly was reached, and the test brought none of its own: crontab -l",
             lambda: quiet(["crontab", "-l"], good)),
            ("a stand-in that fails loudly was reached, and the test brought none of its own: loginctl show-user",
             lambda: quiet(["/bin/sh", "-c", "loginctl show-user nobody; exit 0"], good)),     # a child of a child
        ]
        for words, act in kinds:
            res = planted(act)
            self.assertEqual((res.testsRun, len(res.failures), len(res.errors)), (1, 1, 0), words)
            self.assertIn("guard: test_planted: ", res.failures[0][1], words)
            self.assertIn(words, res.failures[0][1])
            res = planted(act, out=True)
            self.assertTrue(res.wasSuccessful() and res.testsRun == 1, (words, res.failures, res.errors))
            self.assertFalse(os.path.lexists(probe), words)
        # a signal to a process that is not the test's (G7.11): caught in, delivered out
        res = planted(lambda: os.kill(self.stranger(nap, away), signal.SIGTERM))
        self.assertEqual(len(res.failures), 1)
        self.assertIn("not shown to be its own", res.failures[0][1])
        # a planted test that reaches nothing passes with the guard in, and a stand-in of its own is no reach
        mine = os.path.join(self.bin, "crontab")
        with open(mine, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(mine, 0o755)
        for act in (lambda: None, lambda: quiet([true], good), lambda: quiet(["crontab", "-l"], good),
                    lambda: quiet([TMUX, "-V"], good), lambda: fread(SCRIPT), lambda: os.listdir(self.tmp)):
            res = planted(act)
            self.assertTrue(res.wasSuccessful() and res.testsRun == 1, (res.failures, res.errors))
        # THE GUARD HAS NO SWITCH: taken out by anything but this test's planted run, it fails the test that meets it
        res = planted(lambda: _GUARD_OUT.append(object()))
        self.assertEqual(len(res.failures), 1)
        self.assertIn("the guard was taken out outside the gate test's planted run; it is back in", res.failures[0][1])
        self.assertEqual(_GUARD_OUT, [])
        res = planted(lambda: open(os.path.join(probe, "none")))
        self.assertEqual(len(res.failures), 1, "the guard is in again")
        # no setting sends the suite's folders into the real home: the run stops before it makes anything
        run = subprocess.run([sys.executable, "-I", "-B", os.path.abspath(__file__), "-k", "no_such_test"],
                             env=dict(os.environ, TARDIGRADE_TEST_TMP=probe), capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("The suite stopped before making anything: its temporary folders (TARDIGRADE_TEST_TMP) would be "
                      "inside the home of the account that runs it. Nothing was made or changed.", run.stderr)
        self.assertFalse(os.path.lexists(probe))
        # the stand-ins are first on the run's own PATH and on every test's, and the one real program is a link
        for nm in PROGRAMS:
            self.assertEqual(shutil.which(nm, path=os.environ["PATH"]), os.path.join(STUBS, nm), nm)
            self.assertTrue(_under(shutil.which(nm, path=self.path), [self.bin, STUBS]), nm)
        self.assertEqual(os.path.realpath(TMUX), os.path.realpath(_REAL_TMUX))

    def stranger(self, argv, env):
        return self.orphan(argv, env)[0]


class Invariants(unittest.TestCase):
    def setUp(self):
        self.src = fread(SCRIPT)

    def test_INV1_it_never_ends_a_session(self):
        for word in ("kill-server", "kill-pane", "kill-window", "SIGKILL", "pkill", "killall"):
            self.assertNotIn(word, self.src, word)
        # the one tmux session it ends is its own waiting screen, from one function, after checking the mark
        self.assertEqual(self.src.count("kill-session"), 1)
        end = self.src[self.src.index("def end_waiting_screen("):self.src.index("def write_progress(")]
        self.assertIn("kill-session", end)
        self.assertLess(end.index("if is_waiting(sock, name):"), end.index("kill-session"))
        # SIGTERM appears only where the waiting screen says it ignores it
        self.assertEqual([ln.strip() for ln in self.src.splitlines() if "SIGTERM" in ln],
                         ["for sg in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGTSTP):"])
        import re
        self.assertEqual(set(re.findall(r"os\.kill\(([^)]*)\)", self.src)), {"pid, 0"})   # only "is it alive?"

    def test_INV2_it_types_two_things_only_each_from_one_function(self):
        # the opt-in trust answer (G2.12, G2.13) and its own wake-up note (G2.2, G2.20); nothing else, anywhere
        trust = self.src[self.src.index("def answer_trust(r):"):self.src.index("QUESTION = re.compile")]
        note = self.src[self.src.index("def type_note(r, note):"):self.src.index("class Watch:")]
        self.assertEqual((trust.count("send-keys"), note.count("send-keys")), (2, 2))
        self.assertEqual(self.src.replace(trust, "").replace(note, "").count("send-keys"), 0)
        self.assertNotIn("send-text", self.src)
        self.assertIn('"answer_trust": False,', self.src)           # off by default
        self.assertEqual(self.src.count("type_note(r, "), 2)        # its definition and one call, in the watch
        call = self.src.index("type_note(r, self.note_for")
        self.assertIn('sj.get("status") == "idle" and not QUESTION.search(near_box(screen, 15))', self.src[call - 400:call])

    def test_INV6_standard_library_only_and_no_network(self):
        import re
        mods = set(re.findall(r"^\s*import (\w+)", self.src, re.M)) | set(re.findall(r"^\s*from (\w+) import", self.src, re.M))
        self.assertLessEqual(mods, {"glob", "grp", "hashlib", "pwd", "json", "os", "re", "shlex", "shutil", "stat", "subprocess", "sys", "time", "datetime",
                                    "signal", "termios", "select",    # the waiting screen (G4.4): standard, no network
                                    "ctypes",    # macOS only: a process's own argument list from the kernel (G2.39)
                                    "zoneinfo"})  # the zone a limit reply names, to read when it resets (G2.48)


    def test_G5_15_every_environment_read_is_on_one_list(self):
        # T78, family rule 27: every setting the released tool reads has an owner (a person's, a fact from the host,
        # our own marker); test settings take two keys. environment.json holds the lists; this reads the code.
        tree = ast.parse(fread(SCRIPT))

        def environ(n):
            return (isinstance(n, ast.Attribute) and n.attr == "environ" and isinstance(n.value, ast.Name)
                    and n.value.id == "os")

        def strs(n):
            return [x.value for x in ast.walk(n) if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        found, loose = set(), []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                f = n.func
                if (f.attr == "get" and environ(f.value)) or (f.attr == "getenv" and isinstance(f.value, ast.Name)
                                                              and f.value.id == "os"):
                    a = n.args[0] if n.args else None
                    if isinstance(a, ast.Constant):
                        found.add(a.value)
                    else:
                        loose.append(n.lineno)
                if f.attr == "expanduser" and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value.startswith("~"):
                    found.add("HOME")
            if isinstance(n, ast.Subscript) and environ(n.value):
                if isinstance(n.slice, ast.Constant):
                    found.add(n.slice.value)
                else:
                    loose.append(n.lineno)
            if isinstance(n, ast.Compare) and any(environ(c) for c in n.comparators) and isinstance(n.left, ast.Constant):
                found.add(n.left.value)
            if (isinstance(n, ast.For) and isinstance(n.target, ast.Name)
                    and not any(isinstance(x, (ast.Call, ast.Attribute)) for x in ast.walk(n.iter))
                    and any(isinstance(x, ast.Name) and x.id == n.target.id for b in n.body for y in ast.walk(b)
                            if (isinstance(y, ast.Call) and isinstance(y.func, ast.Attribute) and environ(y.func.value))
                            or (isinstance(y, ast.Subscript) and environ(y.value)) for x in ast.walk(y))):
                found.update(strs(n.iter))      # for k in ("A", "B"): ... os.environ.get(k)
        # a read by a name held in a variable must sit in a loop over names written out, read above
        loops = {x.lineno for n in ast.walk(tree) if isinstance(n, ast.For) for x in ast.walk(n) if hasattr(x, "lineno")}
        self.assertEqual([ln for ln in loose if ln not in loops], [], "an environment read by a name the test cannot see")
        e = jload(os.path.join(ROOT, "environment.json"))
        listed = {}
        for kind in ("person", "host", "markers", "test_two_key"):
            for x in e[kind]:
                self.assertNotIn(x["name"], listed, f"{x['name']} is on two lists")
                listed[x["name"]] = kind
        self.assertEqual(sorted(found - set(listed)), [], "read by the tool, on no list")
        self.assertEqual(sorted(set(listed) - found), [], "on a list, no longer read by the tool")
        for x in e["person"]:
            self.assertTrue(x.get("read_about") and x.get("said"), x["name"])
        for x in e["host"]:
            self.assertTrue(x.get("read") and x.get("not_there") and x.get("could_not_be_read"), x["name"])
        for n in found:              # a test setting is read only behind both keys
            if n.startswith("TARDIGRADE_FAKE_"):
                self.assertEqual(listed[n], "test_two_key", n)
        import re
        src = fread(SCRIPT)
        for m in re.finditer(r'os\.environ(?:\.get\(|\[)"(TARDIGRADE_FAKE_[A-Z0-9_]+)"', src):
            start = src.rindex("\n", 0, m.start()) + 1
            line = src[start:src.index("\n", m.end())]
            above = src[src.rindex("\n", 0, start - 1) + 1:start]        # the guard may be the line above
            self.assertTrue("TESTING" in line or ("TESTING" in above and above.strip().startswith("if ")),
                            f"{m.group(1)} read without the two keys: {line.strip()}")


class Promises(unittest.TestCase):
    """PROMISES.json holds what real machines, and the people using them, showed must change, and the version
    each change is due in. A lesson is a gate, not a note: these tests refuse a version while a promise due in it
    is not built, and the version after it until the promise was seen to work on a real machine."""

    def setUp(self):
        import re
        self.rows = jload(os.path.join(ROOT, "PROMISES.json"))["rows"]
        self.version = self.v(re.search(r'^VERSION = "([0-9.]+)"', fread(SCRIPT), re.M).group(1))
        self.tests = set(gate_collected(fread(os.path.abspath(__file__)))[0])     # what the runner collects (G7.3)
        self.plan = fread(os.path.join(ROOT, "DNA-STRAND-MASTER-PLAN.md"))

    @staticmethod
    def v(s):
        return tuple(int(x) for x in s.split("."))

    def test_PROMISES_every_row_is_whole_and_its_codon_is_in_the_plan(self):
        ids = [r["id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)), "two rows share an id")
        for r in self.rows:
            for k in ("id", "seen", "promise", "codon", "owner", "first_ship_in", "ship_in"):
                self.assertTrue(r.get(k), f"{r.get('id')}: '{k}' is empty")
            self.assertIn(r["owner"], ("person", "maintainer"), r["id"])
            self.assertIn(f"| {r['codon']} |", self.plan, f"{r['id']}: codon {r['codon']} is not in the plan")

    def test_PROMISES_a_lesson_not_taken_is_written_down_with_its_reason(self):
        for d in jload(os.path.join(ROOT, "PROMISES.json")).get("declined", []):
            self.assertTrue(d.get("heard") and d.get("why"), "a declined lesson needs what was heard and why it was not taken")

    def test_PROMISES_no_version_ships_while_a_promise_due_in_it_is_not_built(self):
        for r in self.rows:
            if self.v(r["ship_in"]) <= self.version:
                self.assertIn(r.get("test"), self.tests,
                              f"{r['id']} ({r['promise'][:60]}...) is due in {r['ship_in']} and has no test. "
                              "Build it, or have its date moved (see how_to_use in PROMISES.json).")
                self.assertIn(f"`{r['test']}`", self.plan, f"{r['id']}: its test is not named in the plan")

    def test_PROMISES_the_version_after_needs_proof_on_a_real_machine(self):
        for r in self.rows:
            if self.v(r["ship_in"]) < self.version:
                self.assertTrue(r.get("proven"), f"{r['id']} shipped in {r['ship_in']} and no real machine has proven it yet")

    def test_PROMISES_a_date_moves_only_with_a_reason_and_a_persons_row_only_by_his_word(self):
        for r in self.rows:
            if r["ship_in"] != r["first_ship_in"]:
                self.assertTrue(r.get("moved_why"), f"{r['id']}: its date moved with no reason written")
                if r["owner"] == "person":
                    self.assertTrue(r.get("moved_by_person"), f"{r['id']}: only the person who saw it can move its date")


class Strand(unittest.TestCase):
    def test_every_proof_named_in_the_plan_is_a_real_test_and_every_test_has_a_codon(self):
        import re
        plan = fread(os.path.join(ROOT, "DNA-STRAND-MASTER-PLAN.md"))
        named = set(re.findall(r"`(test_[A-Za-z0-9_]+)`", plan))
        src = fread(os.path.abspath(__file__))
        real = {n for n in gate_collected(src)[0] if re.match(r"test_(?:G|INV)", n)}   # what the runner collects
        self.assertEqual(named - real, set(), "the plan names tests that do not exist")
        self.assertEqual(real - named, set(), "tests with no codon in the plan")
        self.assertEqual(gate_problems(src, gate_named()), [], "a named test the runner would not really run")

    def test_G7_3_a_test_named_for_a_claim_counts_only_if_the_runner_really_runs_it(self):
        """the six ways sister skills broke their own gates, planted in a copy of this file"""
        src, name = fread(os.path.abspath(__file__)), "test_G2_49_a_start_finds_claude_by_the_path_setting_not_by_the_boot_services_path"
        self.assertEqual(gate_problems(src, {name}), [])
        a = src.index(f"    def {name}("); b = src.index("\n    def ", a + 10) + 1
        blk = src[a:b]
        ind = lambda t: "".join("    " + l if l.strip() else l for l in t.splitlines(True))
        planted = {
            "nested in a helper after its return": src[:a] + "    def _helper(self):\n        return\n" + ind(blk) + src[b:],
            "renamed so it is not collected": src[:a] + blk.replace(f"def {name}(", "def check" + name[4:] + "(") + src[b:],
            "under if False": src[:a] + "    if False:\n" + ind(blk) + src[b:],
            "marked as skipped": src[:a] + '    @unittest.skip("planted")\n' + blk + src[b:],
            "skipped with skipTest": src[:a] + blk.replace('"""\n', '"""\n        self.skipTest("planted")\n', 1) + src[b:],
            "skipIf with no reason": src[:a] + "    @unittest.skipIf(True)\n" + blk + src[b:],
            "skipIf(True) with a reason": src[:a] + '    @unittest.skipIf(True, "needs Linux")\n' + blk + src[b:],
            "skipUnless on a sum": src[:a] + '    @unittest.skipUnless(1 == 2, "needs tmux")\n' + blk + src[b:],
            "skipTest under if True": src[:a] + blk.replace('"""\n', '"""\n        if True:\n            self.skipTest("needs Linux")\n', 1) + src[b:],
            "a return before its first line": src[:a] + blk.replace('"""\n', '"""\n        return\n', 1) + src[b:],
            "shadowed by a later empty test": src[:b] + f"    def {name}(self):\n        pass\n\n" + src[b:],
            "only its text in a string at column 0": src[:a] + src[b:] + f'\nPLANTED = """\ndef {name}(self):\n"""\n',
        }
        for why, bad in planted.items():
            self.assertNotEqual(gate_problems(bad, {name}), [], f"the gate let through a test {why}")
        # an honest skip on a condition of the machine, with its reason, is "not run here"
        honest = src[:a] + '    @unittest.skipUnless(sys.platform == "plan9", "needs Plan 9")\n' + blk + src[b:]
        self.assertEqual(gate_problems(honest, {name}), [])
        # and after a run, a named test that did not pass is refused; a machine skip is listed, not counted
        bad, here = runner_problems({"test_a", "test_b", "test_c"}, {"test_a"}, {"test_c": "needs Linux and tmux"})
        self.assertEqual(bad, ["test_b: did not pass in this run"])
        self.assertEqual(here, ["test_c (needs Linux and tmux)"])

    def test_G7_6_the_validator_step_fails_when_it_cannot_run(self):
        # T62: when the runner's gh had no `skill` command, the step printed a warning and passed: the check could
        # pass without having run
        wf = fread(os.path.join(ROOT, ".github", "workflows", "tests.yml"))
        step = wf[wf.index("Agent Skills validator"):]
        self.assertIn("\n          gh skill publish --dry-run\n", step)          # the validator's own pass or fail
        self.assertRegex(step, r"if ! gh skill --help >/dev/null 2>&1; then\n(?:(?!\bfi\b)[\s\S])*\bexit 1\n\s+fi\n", "cannot run: fails")
        for soft in ("::warning::", "|| true", "continue-on-error", "else"):
            self.assertNotIn(soft, step, "a way for the step to pass without the validator's pass")
        self.assertNotIn("continue-on-error", wf)

    def test_G7_5_the_workflow_names_its_runner_images_and_runs_every_pushed_head_once(self):
        """T61: `ubuntu-latest` moved under a sister skill and took a Python with it; a push to a branch with a pull
        request open ran everything twice; and when pushes to such a branch ran through the pull request only, a
        pull request that conflicted with main got no run at all, and its head sat with none"""
        wf = fread(os.path.join(ROOT, ".github", "workflows", "tests.yml"))
        self.assertEqual(workflow_problems(wf), [])
        # the check itself: each of these must be refused
        bad = {"latest": wf.replace("runs-on: ubuntu-24.04", "runs-on: ubuntu-latest", 1),
               "an image from an expression": wf.replace("runs-on: ubuntu-24.04", "runs-on: ${{ matrix.os }}", 1),
               "a job with no image": wf.replace("    runs-on: ubuntu-24.04\n", "", 1),
               "tags too": wf.replace('    branches: ["**"]\n', ""),
               "main and release branches only": wf.replace('["**"]', '[main, "release-*"]'),
               "a job that an own pull request runs again": wf.replace("    if: " + WORKFLOW_IF + "\n", "", 1),
               "a job for own pull requests only": wf.replace("full_name != github.repository", "full_name == "
                                                              "github.repository", 1),
               "the old line": re.sub(r"(?ms)^on:\n.*?^permissions:", "on: [push, pull_request]\npermissions:", wf),
               "no pull requests": wf.replace("  pull_request:\n", ""),
               "no group": re.sub(r"(?m)^concurrency:\n(  .*\n)+", "", wf),
               "one group for everything": re.sub(r"(?m)^  group: .*$", "  group: tests", wf),
               "a group by branch": re.sub(r"(?m)^  group: .*$", "  group: tests-${{ github.ref }}", wf),
               "a group by run": re.sub(r"(?m)^  group: .*$", "  group: tests-${{ github.run_id }}", wf),
               "ends runs in pull requests only": re.sub(r"(?m)^  cancel-in-progress: .*$", "  cancel-in-progress: "
                                                         "${{ github.event_name == 'pull_request' }}", wf),
               "ends runs everywhere": re.sub(r"(?m)^  cancel-in-progress: .*$", "  cancel-in-progress: true", wf),
               "ends nothing": re.sub(r"(?m)^  cancel-in-progress: .*$", "  cancel-in-progress: false", wf),
               "a Mac job": wf + "  mac:\n    runs-on: macos-14\n    steps:\n      - run: true\n"}
        for what, text in bad.items():
            self.assertNotEqual(text, wf, f"{what}: the stand-in workflow is the real one")
            self.assertNotEqual(workflow_problems(text), [], f"{what} passed")

    def test_G7_4_every_place_that_states_the_version_says_the_same_one(self):
        """the plugin manifest said 0.1.7 for three releases while the script moved on (T50): the script, the plugin
        manifest, the marketplace entry and the skill file (if they carry a version) and the changelog's top entry"""
        import re
        v = re.search(r'^VERSION = "([^"]+)"', fread(os.path.join(ROOT, "scripts", "tardigrade.py")), re.M).group(1)
        said = {"plugin.json": json.loads(fread(os.path.join(ROOT, ".claude-plugin", "plugin.json"))).get("version")}
        for e in json.loads(fread(os.path.join(ROOT, ".claude-plugin", "marketplace.json"))).get("plugins", []):
            if "version" in e:
                said["marketplace.json " + e.get("name", "?")] = e["version"]
        import glob
        for f in glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")):
            m = re.search(r"^version:\s*['\"]?([^'\"\s]+)", fread(f).split("---", 2)[1], re.M)
            if m:
                said[os.path.relpath(f, ROOT)] = m.group(1)
        said["CHANGELOG.md top entry"] = re.search(r"^## (\S+)", fread(os.path.join(ROOT, "CHANGELOG.md")), re.M).group(1)
        # G7.9 (T76): every clone line in the README names this version's tag, never the default branch
        for i, tag in enumerate(clone_tags(fread(os.path.join(ROOT, "README.md"))), 1):
            said[f"README.md clone line {i}"] = tag[1:] if tag.startswith("v") else tag
        # G7.7 (T63): on the 0.1.9 release branch the README's status line still said 0.1.7, and its test count 65
        # while the suite ran 119
        readme = fread(os.path.join(ROOT, "README.md"))
        m = re.search(r"^> \*\*Status: ([0-9.]+),", readme, re.M)
        said["README.md status line"] = m.group(1) if m else "(no status line)"
        self.assertEqual({k: x for k, x in said.items() if x != v}, {}, f"the script says {v}")
        m = re.search(r"^- (\d+) tests on Linux", readme, re.M)
        self.assertEqual(int(m.group(1)) if m else None, len(re.findall(r"^    def test_", fread(__file__), re.M)),
                         "the README's count of tests is the number of tests in this file")
        # the clone line check itself: a bare clone, a branch and an older tag are each caught
        self.assertEqual(clone_tags("git clone https://example/x\n  git clone --branch main https://example/x\n"
                                    "git clone --depth 1 --branch v0.1.8 https://example/x\n"),
                         ["(no --branch: the default branch)", "main", "v0.1.8"])

    def test_G5_20_the_readme_says_beside_every_clone_line_that_gits_not_a_commit_warning_is_only_a_warning(self):
        # T90: seen on a Mac with git 2.55: the README's own clone line printed "warning: refs/tags/... is not a
        # commit!" and the clone was right (exit 0). Read cold, by a person or an agent, that looks like a failed clone.
        lines = fread(os.path.join(ROOT, "README.md")).splitlines()
        clones = [i for i, ln in enumerate(lines) if "git clone" in ln]
        self.assertTrue(clones, "the README has a clone line")
        for i in clones:
            near = " ".join(lines[i:i + 8])
            for words in ("is not a commit!", "the clone is still right", "exit 0"):
                self.assertIn(words, near, f"beside the clone line {lines[i]!r}")

    def test_G5_16_no_shown_text_removes_the_plugin_catalog(self):
        """T77: Claude Code's `plugin marketplace remove` also uninstalls every plugin from that catalog and deletes
        their saved options and data; the code and every text shown to a person never run it or give it as a step"""
        import glob, re
        places = [os.path.join(ROOT, "scripts", "tardigrade.py"), os.path.join(ROOT, "README.md"),
                  os.path.join(ROOT, "CHANGELOG.md")] + glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md"))
        said = {os.path.relpath(f, ROOT): n for f in places
                for n, line in enumerate(fread(f).splitlines(), 1) if re.search(r"marketplace\s+(remove|rm)\b", line)}
        self.assertEqual(said, {}, "a shown text or the code removes the catalog")

    def test_G5_13_the_repositorys_own_catalog_entry_is_pinned_to_the_commit_its_release_tag_points_to(self):
        """T64: a `./` source hands out whatever the default branch holds under a released label; a `sha` that is an
        annotated tag's own id is not a commit, and git only warns about it"""
        cat = json.loads(fread(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
        mine = [e for e in cat.get("plugins", []) if e.get("name") == "windstorm-tardigrade"]
        self.assertEqual(len(mine), 1, "the entry is there once: pinned, never removed")
        src = mine[0].get("source")
        url = src.get("url", "") if isinstance(src, dict) else ""
        self.assertEqual(catalog_entry_problems(mine[0], lambda ref: catalog_ref_commit(ROOT, url, ref)), [])
        # the check itself: each of these must be refused
        tmp = tempfile.mkdtemp(prefix="tgcat")
        self.addCleanup(shutil.rmtree, tmp, True)
        git = ["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
               "-c", "tag.gpgsign=false"]
        for a in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "one"], ["tag", "-a", "v9.9.9", "-m", "a release"],
                  ["tag", "v9.9.8"]):
            subprocess.run(git + a, check=True, capture_output=True, env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})
        ask = lambda *a: subprocess.run(["git", "-C", tmp] + list(a), capture_output=True, text=True).stdout.strip()
        commit, tag_id = ask("rev-parse", "HEAD"), ask("rev-parse", "refs/tags/v9.9.9")
        self.assertNotEqual(commit, tag_id, "the stand-in release tag is an annotated one")
        self.assertEqual(catalog_ref_commit(tmp, "", "v9.9.9"), commit)
        self.assertEqual(catalog_ref_commit(tmp, "", "v9.9.8"), commit, "a plain tag is its commit")
        self.assertEqual(catalog_ref_commit("", "file://" + tmp, "v9.9.9"), commit, "asked of the remote: the commit too")
        self.assertEqual(catalog_ref_commit("", "file://" + tmp, "v9.9.8"), commit)
        self.assertIsNone(catalog_ref_commit(tmp, "file://" + tmp, "v9.9.7"), "no such tag: no answer, never a guess")
        where = lambda ref: catalog_ref_commit(tmp, "", ref)
        good = {"name": "x", "source": {"source": "url", "url": "https://github.com/o/r.git", "ref": "v9.9.9", "sha": commit}}
        self.assertEqual(catalog_entry_problems(good, where), [])
        bad = {"a ./ source": {**good, "source": "./"},
               "no ref": {**good, "source": {k: v for k, v in good["source"].items() if k != "ref"}},
               "no sha": {**good, "source": {k: v for k, v in good["source"].items() if k != "sha"}},
               "a branch as ref": {**good, "source": {**good["source"], "ref": "main"}},
               "the tag's own id": {**good, "source": {**good["source"], "sha": tag_id}},
               "a short sha": {**good, "source": {**good["source"], "sha": commit[:12]}},
               "a tag that is not there": {**good, "source": {**good["source"], "ref": "v9.9.7"}},
               "a github shorthand": {**good, "source": {"source": "github", "repo": "o/r", "ref": "v9.9.9", "sha": commit}},
               "not https": {**good, "source": {**good["source"], "url": "git@github.com:o/r.git"}}}
        for what, entry in bad.items():
            self.assertNotEqual(catalog_entry_problems(entry, where), [], f"{what} passed")

    def test_no_codon_is_used_twice_in_the_plan(self):
        import re, collections
        plan = fread(os.path.join(ROOT, "DNA-STRAND-MASTER-PLAN.md"))
        seen = collections.Counter(re.findall(r"^\| ((?:G|INV)[0-9.]+) \|", plan, re.M))
        self.assertEqual([c for c, n in seen.items() if n > 1], [], "a codon is used for two claims")


def catalog_ref_commit(root, url, ref):
    """the commit a release tag points to (G5.13, T64): from this clone when the tag is in it, else asked of the
    remote; None when neither says. An annotated tag's own id is never the answer."""
    if root:
        r = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet", f"refs/tags/{ref}^{{commit}}"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    if not url:
        return None
    r = subprocess.run(["git", "ls-remote", url, f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"],
                       capture_output=True, text=True, timeout=120)
    got = dict(reversed(line.split("\t", 1)) for line in r.stdout.splitlines() if "\t" in line)
    return got.get(f"refs/tags/{ref}^{{}}") or got.get(f"refs/tags/{ref}")       # the peeled line is the commit


def catalog_entry_problems(entry, commit_of):
    """what is wrong with a catalog entry as a pin to a release (G5.13, T64); commit_of(ref) -> the tag's commit"""
    import re
    src = entry.get("source")
    if not isinstance(src, dict):
        return [f"its source is {src!r}: a path hands out whatever the branch holds"]
    out = []
    if src.get("source") != "url" or not re.match(r"^https://\S+$", src.get("url", "")):
        out.append(f"its source is not an https url source: {src.get('source')!r} {src.get('url')!r}")
    ref, sha = src.get("ref", ""), src.get("sha", "")
    if not re.match(r"^v\d+(\.\d+){2,3}$", ref):
        out.append(f"its ref {ref!r} is not a release tag")
    if not re.match(r"^[0-9a-f]{40}$", sha):
        out.append(f"its sha {sha!r} is not a whole commit id")
    if not out:
        at = commit_of(ref)
        if at is None:
            out.append(f"where {ref} points could not be read: not a tag in this clone, and the remote did not say")
        elif at != sha:
            out.append(f"its sha {sha} is not the commit {ref} points to ({at})")
    return out


WORKFLOW_IF = "github.event_name == 'push' || github.event.pull_request.head.repo.full_name != github.repository"
_RELEASE = "(github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/heads/release-'))"
WORKFLOW_GROUP = ("tests-${{ github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number) || "
                  + _RELEASE + " && github.run_id || github.ref }}")
WORKFLOW_CANCEL = "${{ github.event_name == 'pull_request' || !" + _RELEASE + " }}"


def workflow_problems(wf):
    """what in a workflow breaks G7.5 (T61); read as text: the suite has no YAML reader and needs none"""
    import re
    out = []
    jobs = re.split(r"(?m)^(?=  [A-Za-z_][\w-]*:\s*$)", wf.split("\njobs:\n", 1)[1]) if "\njobs:\n" in wf else []
    jobs = [j for j in jobs if j.strip()]
    if not jobs:
        out.append("no jobs found")
    for j in jobs:
        name = j.split(":", 1)[0].strip()
        images = re.findall(r"(?m)^    runs-on:\s*(.*?)\s*$", j)
        if len(images) != 1 or not re.match(r"^[a-z]+-\d+(\.\d+)?$", images[0]):
            out.append(f"job {name}: its runner image is {images or 'not named'}, not one named with its version")
        if images and images[0].startswith("macos"):
            out.append(f"job {name}: a Mac job. On a pull request it runs the floor's Python only, the full matrix on "
                       "main and release-*: teach this check that rule with the job, it does not know one yet")
    if "latest" in re.sub(r"(?m)#.*$", "", wf):
        out.append("the word latest is in the workflow")
    m = re.search(r"(?ms)^on:\n(.*?)^(?=\S)", wf)
    on = re.sub(r"(?m)\s*#.*$", "", m.group(1)) if m else ""
    if [x.strip() for x in on.splitlines() if x.strip()] != ["push:", 'branches: ["**"]', "pull_request:"]:
        out.append(f"it does not run on a push to every branch (and to no tag) and on pull requests: {on!r}")
    for j in jobs:      # a pull request from an own branch starts no jobs: its head was run by its push
        if re.findall(r"(?m)^    if:\s*(.*?)\s*$", j) != [WORKFLOW_IF]:
            out.append(f"job {j.split(':', 1)[0].strip()}: it does not run on a push, and on a pull request from "
                       "another repository only")
    m = re.search(r"(?m)^concurrency:\n  group: (.*)\n  cancel-in-progress: (.*)$", wf)
    if not m:
        out.append("no concurrency group at the top of the workflow")
    else:
        group, cancel = m.group(1).strip(), m.group(2).strip()
        if group != WORKFLOW_GROUP:
            out.append("the group is not one per pull request, one per run on main and release-*, and one per branch "
                       f"otherwise: {group}")
        if cancel != WORKFLOW_CANCEL:
            out.append(f"an older run is ended on main or a release branch, or nowhere: {cancel}")
    if len(re.findall(r"(?m)^concurrency:", wf)) + len(re.findall(r"(?m)^    concurrency:", wf)) != 1:
        out.append("more than one concurrency group")
    return out


def clone_tags(text):
    """the tag or branch each `git clone` line names (G7.9, T76); a line without --branch clones the default branch"""
    import re
    out = []
    for line in text.splitlines():
        if re.match(r"\s*git clone\b", line):
            m = re.search(r"(?:--branch|-b)[ =](\S+)", line)
            out.append(m.group(1) if m else "(no --branch: the default branch)")
    return out


# ---------- the gate: a test named for a claim must be one the runner runs (G7.3, T49) ----------
def gate_named():
    """every test the plan or PROMISES.json names, at any version: built rows count whenever they are due"""
    import re
    plan = fread(os.path.join(ROOT, "DNA-STRAND-MASTER-PLAN.md"))
    rows = json.loads(fread(os.path.join(ROOT, "PROMISES.json")))["rows"]
    return set(re.findall(r"`(test_[A-Za-z0-9_]+)`", plan)) | {r["test"] for r in rows if r.get("test")}


def _deco_kind(d):
    f = d.func if isinstance(d, ast.Call) else d
    return (f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""), d


def _machine_free(expr):
    """True if a skip's condition can be worked out without the machine (True, 1 == 1, not False): it names nothing,
    so it is the same everywhere, and a skip on it is a skip whatever the machine, whatever its reason says"""
    if any(isinstance(x, (ast.Name, ast.Attribute, ast.Call)) for x in ast.walk(expr)):
        return False
    try:
        eval(compile(ast.Expression(expr), "<gate>", "eval"), {"__builtins__": {}}, {})
        return True
    except Exception:
        return False


def gate_collected(src):
    """{test name: [(class, its function node, the class chain's decorators)]} for tests the runner collects: a
    method defined straight in a class body at the top of the file. Read from the syntax tree, never from text."""
    tree = ast.parse(src)
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}

    def chain(c, seen=()):
        out = list(c.decorator_list)
        for b in c.bases:
            if isinstance(b, ast.Name) and b.id in classes and b.id not in seen:
                out += chain(classes[b.id], seen + (c.name,))
        return out
    found, twice = {}, []
    for c in classes.values():
        mine = {}
        for f in c.body:
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name.startswith("test"):
                if f.name in mine:
                    twice.append(f"{f.name}: defined twice in {c.name}; only the last one runs")
                mine[f.name] = f
        for n, f in mine.items():
            found.setdefault(n, []).append((c.name, f, chain(c)))
    return found, twice


def gate_problems(src, named):
    """why each named test would not prove its claim: not collected, defined twice, skipped whatever the machine,
    skipped with no reason, or a body that does nothing. An honest skip (skipIf/skipUnless on a condition of the
    machine, with its reason) is "not run here", not a problem."""
    found, probs = gate_collected(src)
    probs = [p for p in probs if p.split(":")[0] in named]
    for n in sorted(named):
        defs = found.get(n, [])
        if not defs:
            probs.append(f"{n}: no test the runner collects (missing, renamed, nested, under an if, or only text)")
            continue
        if len(defs) > 1:
            probs.append(f"{n}: defined in {len(defs)} classes")
        cls, f, decos = defs[-1]
        for kind, d in [_deco_kind(d) for d in f.decorator_list + decos]:
            if kind in ("skip", "expectedFailure"):
                probs.append(f"{n}: {kind} whatever the machine")
            elif kind in ("skipIf", "skipUnless") and not (isinstance(d, ast.Call) and len(d.args) + len(d.keywords) >= 2):
                probs.append(f"{n}: {kind} with no reason")
            elif kind in ("skipIf", "skipUnless") and _machine_free(d.args[0] if d.args else d.keywords[0].value):
                probs.append(f"{n}: {kind} on a condition that is the same on every machine")
        body = [b for b in f.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))]
        if not body or all(isinstance(b, ast.Pass) for b in body) or isinstance(body[0], ast.Return):
            probs.append(f"{n}: its body does nothing")
        for b in body:
            if isinstance(b, ast.If) and _machine_free(b.test) and any(
                    isinstance(x, ast.Attribute) and x.attr == "skipTest" for x in ast.walk(ast.Module(b.body, []))):
                probs.append(f"{n}: skipTest under a condition that is the same on every machine")
        for b in body:
            if (isinstance(b, ast.Expr) and isinstance(b.value, ast.Call) and isinstance(b.value.func, ast.Attribute)
                    and b.value.func.attr == "skipTest"):
                probs.append(f"{n}: skipTest whatever the machine")
    return probs


def runner_problems(named, passed, skipped):
    """after a whole run: a named test must have PASSED, or been skipped on a condition of the machine (the gate
    above refuses any other skip). Returns (problems, not run here)."""
    return sorted(f"{n}: did not pass in this run" for n in named - passed - set(skipped)), sorted(
        f"{n} ({skipped[n]})" for n in named & set(skipped))


class Kept(unittest.TextTestResult):
    """G7.3, G7.12: the outcomes the gate counts, taken from the runner itself and never from the text of its report
    (a test can write into that text)"""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.passed_names, self.skip_reasons = set(), {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_names.add(test.id().rsplit(".", 1)[-1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.skip_reasons[test.id().rsplit(".", 1)[-1]] = reason


if __name__ == "__main__":
    # G7.12 (T108): tests/run_whole.py decides whether this run is green, from outside it. No test and no child of
    # one sees where the result goes
    res = os.environ.pop("TARDIGRADE_RUN_RESULT", None)
    # G7.3: the claims count only what this run really passed
    prog = unittest.main(exit=False, testRunner=unittest.TextTestRunner(resultclass=Kept, verbosity=2 if "-v" in sys.argv else 1))
    PASSED, SKIPPED = prog.result.passed_names, prog.result.skip_reasons
    whole = all(a == "-v" for a in sys.argv[1:])        # a run with a filter or a test's name is not the whole
    bad, here = runner_problems(gate_named(), PASSED, SKIPPED) if whole else ([], [])
    for line in bad:
        print("GATE:", line)
    if here:
        print(f"gate: {len(here)} named test(s) not run here: " + "; ".join(here))
    if whole:
        print(f"gate: {len(gate_named() & PASSED)} named test(s) passed in this run")
    ok = prog.result.wasSuccessful() and not bad
    if res and whole:           # the last thing a finished whole run does
        with open(res, "w") as f:
            json.dump({"ran": prog.result.testsRun, "ok": ok}, f)
    sys.exit(0 if ok else 1)
