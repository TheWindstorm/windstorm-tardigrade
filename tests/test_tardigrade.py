"""Tests for Windstorm Tardigrade. Each test is named after the codon it proves (DNA-STRAND-MASTER-PLAN.md).

They run a stand-in `claude` (tests/fake_claude.py) inside a private tmux folder and a private HOME, so no
real session, no real tmux server and no service is touched. Linux only. Run: python3 -m unittest -v
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def jdump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def fread(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


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
FAKE = os.path.join(ROOT, "tests", "fake_claude.py")
FAKE_HERDR = os.path.join(ROOT, "tests", "fake_herdr.py")
TMUX = shutil.which("tmux")
WAIT = float(os.environ.get("TARDIGRADE_TEST_WAIT", "40"))

# Zero survivors: a test's tmux servers and stand-ins end even when the run itself is stopped. unittest runs no
# tearDown or cleanup on SIGTERM (timeout, a stopped job) or SIGHUP (a closed terminal), so the live tests' own
# cleanups run here first. Each test registers its cleanup the moment its folder exists.
LIVE = []


def _end_live(*_):
    for t in list(LIVE):
        t.end_all()


def _on_signal(signum, _frame):
    _end_live()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


for _sig in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(_sig, _on_signal)
import atexit  # noqa: E402
atexit.register(_end_live)


@unittest.skipUnless(sys.platform.startswith("linux") and TMUX, "needs Linux and tmux")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tg", dir=os.environ.get("TARDIGRADE_TEST_TMP", "/tmp"))
        self.pids, self.loose = [], []
        LIVE.append(self)
        self.addCleanup(self.end_all)     # runs even when setUp fails after this line, or tearDown raises
        self.home = os.path.join(self.tmp, "h")
        self.bin = os.path.join(self.tmp, "bin")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.home, self.bin, self.work):
            os.makedirs(d)
        shutil.copy(FAKE, os.path.join(self.bin, "claude"))
        os.chmod(os.path.join(self.bin, "claude"), 0o755)
        self.path = f"{self.bin}:/usr/local/bin:/usr/bin:/bin"
        self.env = {"HOME": self.home, "TMUX_TMPDIR": self.tmp, "PATH": self.path, "SHELL": "/bin/bash",
                    "USER": os.environ.get("USER", "u"), "LANG": "C.UTF-8", "TERM": "xterm-256color",
                    "TARDIGRADE_TESTING": "1", "TARDIGRADE_FAKE_UPTIME_S": "100000", "TARDIGRADE_FAKE_BOOT_ID": "boot-a",
                    "TARDIGRADE_FAKE_X11_DIR": os.path.join(self.tmp, "x11")}
        os.makedirs(os.path.join(self.tmp, "x11"))
        with open(os.path.join(self.tmp, "x11", "X10"), "w") as f:      # this "machine" has a desktop on display :10
            f.write("")
        self.cfg({"path": self.path, "shell": "bash", "stagger_s": 0, "settle_s": 2, "retry_min": 1, "retry_gap_s": 1,
                  "herdr_tabs": False, "tmux": TMUX, "watch_min": 0.1})
        self.sock = os.path.join(self.tmp, f"tmux-{os.getuid()}", "default")

    def end_all(self):
        """end only what this test started, each by its exact process id; every step runs even if one fails"""
        if self not in LIVE:
            return
        LIVE.remove(self)
        steps = [lambda: [self.kill(r.get("pid")) for r in self.launch_rows_as_far_as_written()],
                 lambda: [self.kill(p) for p in self.pids],
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
        mine = self.processes_of_this_test()
        for pid in mine:
            self.kill(pid)
        end = time.time() + 3
        while time.time() < end and any(os.path.exists(f"/proc/{p}") for p in mine):
            time.sleep(0.1)
        shutil.rmtree(self.tmp, ignore_errors=True)

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
    @staticmethod
    def kill(pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, TypeError, ValueError):
            pass

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
        r = subprocess.run([TMUX, "-S", sock, *a], capture_output=True, text=True, env=env or self.env, timeout=30)
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
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
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


class Revive(Base):
    def recorded_then_rebooted(self, **kw):
        sid = self.start_lane("alpha", "Alpha Lane", **kw)
        self.tg("record", "--force")
        self.reboot()
        return sid

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
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
        tg.HOME, tg.STATE = self.home, st
        tg.LOG = os.path.join(st, "tardigrade.log")
        self.assertEqual(tg.exited_at({"session_id": a, "last_seen": seen}), 0)
        os.remove(hp)
        self.reboot()
        rc, out = self.tg("revive")
        self.assertIn("could not be used: it cannot be read", out)

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
        self.assertIn("skip  Echo Lane: a tmux session named echo already exists", out)
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
        os.environ.update(TARDIGRADE_TESTING="1", TARDIGRADE_FAKE_X11_DIR=x11)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("TARDIGRADE_TESTING", "TARDIGRADE_FAKE_X11_DIR")])
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
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
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
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
        self.assertIn('shutil.rmtree(os.path.join(INSTALL_DIR, "__pycache__"), ignore_errors=True)', u)

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
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
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
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
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
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
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
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
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
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
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

    def test_G5_2_setup_dry_run_installs_nothing(self):
        rc, out = self.tg("setup", "--dry-run")
        self.assertIn("would copy", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "systemd")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local", "share", "windstorm-tardigrade")))

    def test_G5_3_setup_and_census_say_who_has_to_log_in_on_a_mac(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tardigrade", SCRIPT)
        tg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tg)
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
        self.assertEqual(gone_soon(left), [], "processes of the stopped run are still alive")
        self.assertEqual(os.listdir(parent), [], "the stopped run left its folder behind")
        time.sleep(3)                                       # and nothing comes back to write in it later
        self.assertEqual(left(), [], "a process of the stopped run came back")
        self.assertEqual(os.listdir(parent), [], "something of the stopped run wrote in its folder afterwards")


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
        self.assertLessEqual(mods, {"glob", "hashlib", "json", "os", "re", "shlex", "shutil", "stat", "subprocess", "sys", "time", "datetime",
                                    "signal", "termios", "select",    # the waiting screen (G4.4): standard, no network
                                    "ctypes",    # macOS only: a process's own argument list from the kernel (G2.39)
                                    "zoneinfo"})  # the zone a limit reply names, to read when it resets (G2.48)


class Promises(unittest.TestCase):
    """PROMISES.json holds what real machines, and the people using them, showed must change, and the version
    each change is due in. A lesson is a gate, not a note: these tests refuse a version while a promise due in it
    is not built, and the version after it until the promise was seen to work on a real machine."""

    def setUp(self):
        import re
        self.rows = jload(os.path.join(ROOT, "PROMISES.json"))["rows"]
        self.version = self.v(re.search(r'^VERSION = "([0-9.]+)"', fread(SCRIPT), re.M).group(1))
        self.tests = set(re.findall(r"def (test_[A-Za-z0-9_]+)\(", fread(os.path.abspath(__file__))))
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
        real = set(re.findall(r"def (test_(?:G|INV)[A-Za-z0-9_]+)\(", fread(os.path.abspath(__file__))))
        self.assertEqual(named - real, set(), "the plan names tests that do not exist")
        self.assertEqual(real - named, set(), "tests with no codon in the plan")

    def test_no_codon_is_used_twice_in_the_plan(self):
        import re, collections
        plan = fread(os.path.join(ROOT, "DNA-STRAND-MASTER-PLAN.md"))
        seen = collections.Counter(re.findall(r"^\| ((?:G|INV)[0-9.]+) \|", plan, re.M))
        self.assertEqual([c for c, n in seen.items() if n > 1], [], "a codon is used for two claims")


if __name__ == "__main__":
    unittest.main()
