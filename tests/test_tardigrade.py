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


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "tardigrade.py")
FAKE = os.path.join(ROOT, "tests", "fake_claude.py")
FAKE_HERDR = os.path.join(ROOT, "tests", "fake_herdr.py")
TMUX = shutil.which("tmux")
WAIT = float(os.environ.get("TARDIGRADE_TEST_WAIT", "40"))


@unittest.skipUnless(sys.platform.startswith("linux") and TMUX, "needs Linux and tmux")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tg", dir="/tmp")
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
        self.cfg({"path": self.path, "shell": "bash", "stagger_s": 0, "settle_s": 2, "retry_min": 1,
                  "herdr_tabs": False, "tmux": TMUX, "watch_min": 0.1})
        self.sock = os.path.join(self.tmp, f"tmux-{os.getuid()}", "default")
        self.pids, self.loose = [], []

    def tearDown(self):
        # end only what this test started, each by its exact process id
        for row in self.launches():
            self.kill(row["pid"])
        for pid in self.pids:
            self.kill(pid)
        self.end_loose()
        for s in self.sockets():
            rc, out = self.tmux(s, "display", "-p", "#{pid}")
            if rc == 0 and out.strip().isdigit():
                self.kill(int(out.strip()))
        shutil.rmtree(self.tmp, ignore_errors=True)

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
            os.kill(pid, signal.SIGTERM)
        except OSError:
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
        p = subprocess.Popen([os.path.join(self.bin, "claude")], cwd=self.work, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                             env=dict(self.env, FAKE_SID=sid, FAKE_NAME=name))
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
        for f in (os.path.join(cfgd, "lanes.json"), tsv, self.state("tardigrade.log"), self.state("last-revive.json"),
                  self.state("progress.json")):
            self.assertEqual(os.stat(f).st_mode & 0o777, 0o600, f)

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
        self.cfg({"settle_s": 3, "starts_at_once": 3, "load_max": 50, "load_wait_s": 0})
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
            self.assertIn("a revive is running", self.tg("record")[1])
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
        waiters = []                                               # this test's own, by the HOME it gave them
        for pid in [x for x in os.listdir("/proc") if x.isdigit()]:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmd = f.read()
                if b"tardigrade.py\0wait" in cmd:
                    with open(f"/proc/{pid}/environ", "rb") as f:
                        if f"HOME={self.home}".encode() in f.read().split(b"\0"):
                            waiters.append(pid)
            except OSError:
                pass
        self.assertEqual(waiters, [], "a waiting screen outlived its turn")
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
        # and the same rule holds the trust answer (G2.12): one pattern for both
        src = fread(SCRIPT)
        call = src.index("type_note(r, self.note_for")
        self.assertIn("not OTHER_DIALOG.search(bottom(screen, 15))", src[call - 500:call])

    def test_G2_38_on_done_is_run_with_the_counts_when_a_restore_ends(self):
        self.script("tell", '#!/bin/sh\necho "$* | $TARDIGRADE_STARTED $TARDIGRADE_LOADED $TARDIGRADE_FAILED" >> "$HOME/told.log"\n')
        self.cfg({"on_done": "tell started={started} loaded={loaded} not_loaded={not_loaded} down={down} failed={failed}",
                  "wake": "idle"})
        for nm in ("alpha", "beta"):
            self.start_lane(nm, nm.title() + " Lane")
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.wait_launches(n + 2)
        self.assertEqual(fread(os.path.join(self.home, "told.log")),
                         "started=2 loaded=2 not_loaded=0 down=0 failed=0 | 2 2 0\n")
        self.assertIn("on_done ran (exit 0)", out)

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
        notes = fread(os.path.join(self.home, "notes.log")).splitlines()
        self.assertEqual(len(notes), 2, out)
        self.assertTrue(notes[0].startswith("Bringing back 2 session(s)"), notes)
        self.assertTrue(notes[1].startswith("Restore done: 2 session(s) brought back, 0 need you"), notes)
        runs = [json.loads(x) for x in fread(os.path.join(self.home, "herdr-runs.jsonl")).splitlines()]
        self.assertEqual([r["body"] for r in runs if "notification" in r], notes)

    def test_G3_7_census_names_a_name_two_sessions_carry_and_a_copy_started_by_hand(self):
        self.cfg({"wake": "idle", "settle_s": 3, "starts_at_once": 1})
        ids = [self.start_lane(n, n.title() + " Lane") for n in ("alpha", "beta")]
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
        self.assertEqual(d["same_name"], {"Beta Lane": sorted([ids[1], hand])}, out)
        self.assertEqual(d["by_hand"], [{"name": "Beta Lane", "session_id": hand, "in_place_of": ids[1]}])
        rc, text = self.tg("census")
        self.assertIn("SAME NAME: more than one session carries it", text)
        self.assertIn("STARTED BY HAND while its session was coming back", text)
        self.assertEqual(text.count("STARTED BY HAND"), 1)                    # the real Beta is not marked

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
        self.assertIn('sj.get("status") == "idle" and not QUESTION.search(bottom(screen, 15))', self.src[call - 400:call])

    def test_INV6_standard_library_only_and_no_network(self):
        import re
        mods = set(re.findall(r"^\s*import (\w+)", self.src, re.M)) | set(re.findall(r"^\s*from (\w+) import", self.src, re.M))
        self.assertLessEqual(mods, {"glob", "hashlib", "json", "os", "re", "shlex", "shutil", "stat", "subprocess", "sys", "time", "datetime",
                                    "signal", "termios", "select",    # the waiting screen (G4.4): standard, no network
                                    "ctypes"})   # macOS only: a process's own argument list from the kernel (G2.39)


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


if __name__ == "__main__":
    unittest.main()
