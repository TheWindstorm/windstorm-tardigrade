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
                    "TARDIGRADE_TESTING": "1", "TARDIGRADE_FAKE_UPTIME_S": "100000", "TARDIGRADE_FAKE_BOOT_ID": "boot-a"}
        self.cfg({"path": self.path, "shell": "bash", "stagger_s": 0, "settle_s": 2, "retry_min": 1,
                  "herdr_tabs": False, "tmux": TMUX})
        self.sock = os.path.join(self.tmp, f"tmux-{os.getuid()}", "default")
        self.pids = []

    def tearDown(self):
        # end only what this test started, each by its exact process id
        for row in self.launches():
            self.kill(row["pid"])
        for pid in self.pids:
            self.kill(pid)
        for s in self.sockets():
            rc, out = self.tmux(s, "display", "-p", "#{pid}")
            if rc == 0 and out.strip().isdigit():
                self.kill(int(out.strip()))
        shutil.rmtree(self.tmp, ignore_errors=True)

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
        self.recorded_then_rebooted()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        last = self.wait_launches(n + 1)[-1]["argv"][-1]
        self.assertIn("this machine restarted", last)
        self.assertIn("background is gone", last)

    def test_G2_3_wake_idle_types_nothing(self):
        self.cfg({"wake": "idle"})
        sid = self.recorded_then_rebooted()
        n = len(self.launches())
        self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        self.assertEqual(self.wait_launches(n + 1)[-1]["argv"][-1], sid)

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

    def test_G2_8_a_machine_wide_login_token_comes_back_and_is_checked(self):
        # every session has the same token, and the person's shell supplies it again: an ordinary revive
        with open(os.path.join(self.home, ".bashrc"), "w") as f:
            f.write("export CLAUDE_CODE_OAUTH_TOKEN=tok-abc\n")
        self.start_lane("alpha", "Alpha Lane", extra_env={"CLAUDE_CODE_OAUTH_TOKEN": "tok-abc"})
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = self.wait_launches(n + 1)[-1]
        self.assertIn("this machine restarted", new["argv"][-1])
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["needs_you"]), (["Alpha Lane"], []), out)

    def test_G2_11_a_pinned_session_comes_back_silent_and_a_changed_login_is_reported(self):
        a = self.start_lane("alpha", "Alpha Lane")
        b = self.start_lane("beta", "Beta Lane", extra_env={"CLAUDE_CODE_OAUTH_TOKEN": "tok-pinned"})
        self.tg("record", "--force")
        self.reboot()
        n = len(self.launches())
        rc, out = self.tg("revive", "--boot", TARDIGRADE_FAKE_UPTIME_S=100)
        new = {x["sid"]: x["argv"] for x in self.wait_launches(n + 2)[n:]}
        self.assertIn("this machine restarted", new[a][-1])      # the ordinary session gets the note
        self.assertEqual(new[b][-1], b)                           # the pinned one is started silent
        rep = jload(self.state("last-revive.json"))
        self.assertEqual((rep["started"], rep["needs_you"]), (["Alpha Lane"], ["Beta Lane"]), out)
        self.assertIn("different login", out)

    def test_G2_9_two_revives_at_once_do_not_both_run(self):
        self.recorded_then_rebooted()
        os.makedirs(self.state("lock"))
        n = len(self.launches())
        rc, out = self.tg("revive")
        self.assertIn("another revive is running", out)
        self.assertEqual(len(self.launches()), n)

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
        self.assertEqual((rep["trust_answered"], rep["not_loaded"]), (["Alpha Lane"], []), out)
        rc, cj = self.tg("census", "--json")
        d = json.loads(cj)
        self.assertEqual((d["running"], d["trust"], d["sessions"]["hidden"][0]["status"]), (1, 0, "idle"), cj)

    def test_G2_13_enter_is_sent_only_with_the_cursor_on_yes(self):
        src = fread(SCRIPT)
        body = src[src.index("def answer_trust(r):"):src.index("def loaded(r):")]
        self.assertEqual(src.count("send-keys"), 2)                 # Down and Enter, nowhere else in the script
        self.assertEqual(body.count("send-keys"), 2)
        enter = body.index('"Enter"')
        self.assertLess(body.rindex("if not YES_LINE.search(screen):", 0, enter), enter)   # checked just before
        self.assertIn('if CFG["answer_trust"] and came:', src)      # reached only through the opt-in
        self.assertEqual(src.count("answer_trust(r)"), 2)           # its definition and one call, in the sweep


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
        self.assertIn("attach -t =alpha", out)


class Invariants(unittest.TestCase):
    def setUp(self):
        self.src = fread(SCRIPT)

    def test_INV1_it_never_ends_a_session(self):
        for word in ("kill-server", "kill-session", "kill-pane", "kill-window", "SIGTERM", "SIGKILL", "pkill", "killall"):
            self.assertNotIn(word, self.src, word)
        import re
        self.assertEqual(set(re.findall(r"os\.kill\(([^)]*)\)", self.src)), {"pid, 0"})   # only "is it alive?"

    def test_INV2_it_never_types_into_a_session_or_answers_a_dialog(self):
        # the one exception is the opt-in trust answer (G2.12, G2.13): two keys, in one function
        body = self.src[self.src.index("def answer_trust(r):"):self.src.index("def loaded(r):")]
        self.assertEqual(self.src.replace(body, "").count("send-keys"), 0)
        self.assertNotIn("send-text", self.src)
        self.assertIn('"answer_trust": False,', self.src)           # off by default

    def test_INV6_standard_library_only_and_no_network(self):
        import re
        mods = set(re.findall(r"^\s*import (\w+)", self.src, re.M)) | set(re.findall(r"^\s*from (\w+) import", self.src, re.M))
        self.assertLessEqual(mods, {"glob", "hashlib", "json", "os", "re", "shlex", "shutil", "stat", "subprocess", "sys", "time", "datetime"})


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
