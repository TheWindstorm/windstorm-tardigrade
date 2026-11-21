# DNA Strand Master Plan: Windstorm Tardigrade

Plan version 1.0 (2026-11-21). Builds: `scripts/tardigrade.py` 0.1.0.

This is the plan the skill is built from. Each gene is one thing the skill does. Each codon is one claim small
enough to test, with its proof next to it. A proof is a test in `tests/test_tardigrade.py`, named after its codon.
A codon with no test says so, under "Not verified".

## The want

Restated from what the person who asked for it said:

1. After a crash, a freeze or a restart, every Claude Code session comes back by itself: the same name, the same
   conversation, in the same place, and it picks up where it left off. Nobody has to remember what was open.
2. At any time, one command says what is running and what is not. Sessions have run in the background for weeks
   with no terminal window open, and nobody knew.

A "lane" is one Claude Code session that has a name and a job.

## The name, and where the likeness stops

A tardigrade (a water bear) is an animal about half a millimetre long. When its water dries up it pulls into a
ball, its metabolism stops, and it waits. When water returns it carries on. This skill is modeled on that: the
machine stops, every session stops with it, and when the machine is back the sessions carry on as themselves.

Where the likeness stops:

- A tardigrade needs time to get ready. Dried too fast, it dies. A power cut gives no warning, so this skill cannot
  rely on getting ready. It writes its record all the time (G1), and a revive needs nothing but that record.
- A tardigrade keeps its own body. A Claude Code session does not keep its process: the process is gone. What comes
  back is the conversation on disk, resumed under a new process. Timers and background jobs do not come back, and the
  session is told so (G2.2).

## Field notes (2026-11-21)

Written before the build. Each line says where it comes from.

**The ecosystem.** Claude Code sessions are processes in a terminal. The conversation is on disk and
`claude --resume <id>` reopens it (Claude Code 2.1.330 `--help`). A reboot ends every process. Claude Code has an
agent view (`claude agents`) for background sessions it started itself; we have not tested what it shows for a
session left running in tmux with no window. We did not find a Claude Code feature that restarts sessions after a
reboot (web search, 2026-11-21).

**The niche is not empty.** Found by web search on 2026-11-21, not tested by us:
tmux-resurrect and tmux-continuum (MIT) restore tmux layouts, and a program comes back only with a restore strategy
written for it. Zellij (MIT) has its own "session resurrection". Small tools aimed at Claude Code exist too:
claude-session-keeper, ccbrb, claude-code-session-restore, and waterbear, which keeps one remote-control session alive
and is also named for the tardigrade. We did not find one that does all four of: starts by itself at boot, refuses to
start a second copy of a session, tells the revived session what it lost, and lists sessions that run with no
window. That is a search result, not a proof that none exists.

**The pressures, from machines that really rebooted.** This skill starts from a restore kit that an AI agent on
The Windstorm's fleet built and has run since 2026-11-05 on a machine with about 100 sessions. What that fleet saw:

- A session resumed twice acts twice. So: never a second copy (G2.4).
- A tmux server started from inside a Claude session passes that session's environment on, and one variable in it
  turns transcript saving off. So: a clean environment for every revived session (G2.7).
- A snapshot taken while a machine is half-started overwrites a good record with a short one. So: no record until
  this boot's revive has finished (G1.3).
- A restore that needs one click fails when nobody is there. So: it runs by itself at boot, once a person has
  switched it on (G5).
- A Mac with FileVault waits at its unlock screen after a restart. One sat there for 6.5 hours with nothing running.
  So: setup says plainly who has to log in (G5.3).
- Timers and watchers inside a session do not survive, and a session that is not told keeps waiting for them.
  So: the note (G2.2).

## Genes and codons

### G1 Record: write down what is running, all the time

| Codon | Claim | Proof |
|---|---|---|
| G1.1 | `record` writes one row per live session of this user: session id, name, folder, model, effort, permission mode, and the tmux socket and session it lives in. | `test_G1_1_record_writes_down_each_live_session` |
| G1.2 | A session started by another session, or one that is not interactive, is not recorded. | `test_G1_2_record_skips_helper_sessions` |
| G1.3 | While the machine is still starting, and (once setup has run) until this boot's revive has finished, `record` writes nothing. | `test_G1_3_record_waits_while_the_machine_is_still_starting` |
| G1.4 | When no session is live, the previous record is kept. | `test_G1_4_record_never_replaces_the_record_with_an_empty_list` |
| G1.5 | A recorded session that is no longer live moves to the "down" list with the time, and the record before each write is kept as `lanes.json.prev`. | `test_G1_5_a_session_that_went_down_moves_to_the_down_list` |
| G1.6 | A token or key is never written to the record or the log. Only the name of the variable that held it is kept. | `test_G1_6_a_token_is_never_written_down` |

### G2 Revive: bring each one back as itself

| Codon | Claim | Proof |
|---|---|---|
| G2.1 | `revive` starts each recorded session that is not running with `claude --resume <its id>`, in its folder, with its model, effort and permission mode, in a tmux session of the same name on the same socket. | `test_G2_1_revive_brings_a_session_back_as_itself` |
| G2.2 | With `"wake": "note"` (the default) the revived session gets one message: the machine restarted, the conversation is intact, background work is gone, check and carry on. | `test_G2_2_the_revived_session_is_told_what_happened` |
| G2.3 | With `"wake": "idle"` nothing is said and the session waits. | `test_G2_3_wake_idle_types_nothing` |
| G2.4 | A session id held by any live process of this user is never started. | `test_G2_4_never_a_second_copy` |
| G2.5 | `revive --boot` acts only on a machine that has just started, and only once per boot. | `test_G2_5_boot_revive_runs_once_per_boot_and_only_after_a_boot` |
| G2.6 | `--dry-run` says what would start and starts nothing. | `test_G2_6_dry_run_starts_nothing` |
| G2.7 | A revived session gets a clean environment: nothing of the calling session's `CLAUDE*` variables reaches it. | `test_G2_7_a_revived_session_gets_a_clean_environment` |
| G2.8 | A session that was started with a token in its environment is started only when the person's shell sets that variable too. Otherwise it is listed as "needs you". | `test_G2_8_a_session_started_with_a_token_is_left_for_the_person` |
| G2.9 | Two revives never run at the same time. | `test_G2_9_two_revives_at_once_do_not_both_run` |
| G2.10 | `revive --only NAME` brings back that one session and no other. | `test_G2_10_only_brings_back_just_that_one` |

### G3 Census: say what is running and what is not

| Codon | Claim | Proof |
|---|---|---|
| G3.1 | `census` lists three groups: running with a window, running with no window open (hidden), and on the record but not running (down). | `test_G3_1_census_names_hidden_and_down_sessions` |
| G3.2 | `census` changes nothing: it starts nothing and writes nothing to the record. | `test_G3_2_census_changes_nothing` |
| G3.3 | On a shared machine, other users' Claude processes are counted by program name only. Their arguments, environment and files are never read. | Not verified by a test (see below). |

### G4 Windows and tabs

| Codon | Claim | Proof |
|---|---|---|
| G4.1 | `open` opens a terminal window for each hidden session. Where it cannot open one, it prints the line to type instead. | `test_G4_1_open_prints_the_attach_line_when_it_cannot_open_a_window` |
| G4.2 | With Herdr, a revived session is attached again in the tab that carries its label, and a tab that is busy or has typed text is left alone. | Not verified in this packaging. |

### G5 Setup

| Codon | Claim | Proof |
|---|---|---|
| G5.1 | `setup` installs a boot service that runs `revive --boot` 45 seconds after start and leaves the tmux server running after it ends, and a timer that records every 5 minutes. | `test_G5_1_the_boot_service_revives_once_and_outlives_itself` |
| G5.2 | `setup --dry-run` installs nothing. | `test_G5_2_setup_dry_run_installs_nothing` |
| G5.3 | `setup` says who has to log in after a restart: on Linux whether lingering is on, on a Mac whether FileVault is on. It never changes either. | Not verified by a test. |
| G5.4 | `uninstall` removes the service and the timer and touches no running session and no record. | Not verified by a test. |

## Invariants

These hold across every gene. A change that breaks one is a plan change first.

| Invariant | Proof |
|---|---|
| INV1 It never ends a Claude session and never ends a tmux session. The one thing it replaces is the leftover shell in a tmux session it made itself, in the same run, when the first start failed. | `test_INV1_it_never_ends_a_session` |
| INV2 It never types into a session and never answers a dialog. The note in G2.2 is given to `claude` as its opening message, not typed. | `test_INV2_it_never_types_into_a_session_or_answers_a_dialog` |
| INV3 It never stores a secret. | G1.6 |
| INV4 Never a second copy of a session. | G2.4, G2.9 |
| INV5 It reads only this user's processes. | G3.3 |
| INV6 Python standard library only, and no network. | `test_INV6_standard_library_only_and_no_network` |

The link between this plan and the tests is itself tested: every proof named here is a real test, and every codon
test has a codon here.

## Proven once by hand, not by a test

- 2026-11-21, Linux, Claude Code 2.1.330, a real session: started in tmux, recorded, ended, then brought back with
  `revive --only`. It came back with the same session id, name, folder and model, its earlier messages on screen, and
  it acted on the note.
- 2026-11-21, a shared Linux machine with 98 sessions: `census` took a quarter of a second, found 98 running and 2
  hidden, and its 98 session ids matched a separate tool's list, 98 of 98.

## Not verified

- **A real reboot.** The boot service has not yet been through one. Until it has, treat G5.1 as proven on paper only.
- **macOS.** The LaunchAgent and the FileVault notice (G5.3) are written and not yet run on a Mac.
- **Herdr** (G4.2). The logic comes from the fleet kit, where it is also not yet proven through a reboot.
- **Opening windows** (G4.1) on a real desktop: only the printed line is tested.
- **G3.3 and INV5.** The check is one line (`uid != me` before anything is read), read by a person, not exercised
  by a test, because a test cannot easily start a process as another user.
- **Sessions pinned to different accounts by a token.** G2.8 can tell that a variable is set, not that it holds the
  same token. Such sessions should be started by hand.
- **Windows.** Not supported.

## Not goals

- It does not restore files, git state, or anything a session was in the middle of writing.
- It does not restart a session that ended while the machine stayed up. `census` shows it as down, and
  `revive --only` brings it back when you ask.
- It does not bring back timers, watchers or background commands. It tells the session they are gone.
- It does not manage other users' sessions.
