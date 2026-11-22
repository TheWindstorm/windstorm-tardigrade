# DNA Strand Master Plan: Windstorm Tardigrade

Plan version 1.7 (2026-11-21). Builds: `scripts/tardigrade.py` 0.1.7.

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
- The person who asked for this skill decided (2026-11-21): answering the trust question is allowed as a setting a
  person switches on, never as a default. Saying yes to a folder is the person's call; switching the setting on is
  how they make that call once, for folders their sessions were already running in (G2.12).
- Claude Code asks "trust this folder?" on every start of a session whose folder is the home folder. A restore
  tool that waits a fixed time for that question can miss it on a slow start and report the session as fine
  (2026-11-21, a real Mac: one session parked there, logged OK). Tardigrade never answers it (INV2). So: the
  census names it (G3.5).
- On a slow machine just after boot, Claude Code can take minutes to load a session. For that time the process
  exists and its session file does not. Plan 1.3 called such a session "down", and a record taken in that window
  would have dropped it (2026-11-21, a real Mac). So: a recorded session that is still loading counts as running
  (G1.8).
- The person who asked for this skill decided (2026-11-21, later the same day): the fleet runs one restore tool,
  and it is this one. The fleet kit's author wrote down everything his kit does, fifteen items, as the list this
  skill has to pass before the fleet switches. Plan 1.7 is that list. What it taught:
  - A command line is public: any `pkill -f WORD` on the machine is matched against it, so free text there puts
    the session in the path of a careless kill. The kit keeps its notice off the command line for that reason.
    Plan 1.6 passed the wake-up note as an argument. So: nothing but the start command is on a command line; the
    note is typed (G2.2, G2.19).
  - A restore tool that maps a permission mode it does not know onto "checks off" brings a session back looser
    than the person left it. So: the mode comes back word for word, and a test says so (G2.14).
  - A session can be pinned to an account by a wrapper command. It has to come back through that wrapper, or it
    comes back on the wrong account (G2.15).
  - Sessions started from a desktop need the desktop's variables (display, session bus) to open a browser or use
    the clipboard, and a boot service has none of them (G2.17).
  - "The process is up" is not "the session is loaded". Count the loaded ones, and name the rest (G2.24).
- The kit's author then reviewed plan 1.7 line by line and found four faults before any machine ran it. The worst:
  the trust answer looked for the question anywhere on the screen. A resumed session redraws its old
  conversation, a conversation can quote that dialog, and a key sent then lands in whatever dialog is really open,
  which on an account at its limit is the one that offers to switch to paid usage credits. So: only the dialog at
  the foot of the screen counts, and nothing is sent when a usage or limit dialog is near (G2.25). The others: a
  machine's tmux server has its own config and its own way to start (G2.26); an environment file copied from a
  desktop carries that desktop's folder and user id (G2.27); and one process list per session is too many on a
  machine with 12,000 processes (G2.28).
- On a Mac reached over ssh, the `claude` command is often a wrapper that supplies a login token, because the
  keychain is not available there. Plan 1.2 refused to start any session that carried a token unless the shell
  set it, and on such a Mac that meant it started nothing (2026-11-21, a real reboot: 0 of 2). So: start through
  the person's own shell and command, then check the login (G2.8, G2.11).

## Genes and codons

### G1 Record: write down what is running, all the time

| Codon | Claim | Proof |
|---|---|---|
| G1.1 | `record` writes one row per live session of this user: session id, name, folder, model, effort, permission mode, and the tmux socket and session it lives in. | `test_G1_1_record_writes_down_each_live_session` |
| G1.2 | A session started by another session, or one that is not interactive, is not recorded. | `test_G1_2_record_skips_helper_sessions` |
| G1.3 | While the machine is still starting, and (once setup has run) until this boot's revive has finished, `record` writes nothing. | `test_G1_3_record_waits_while_the_machine_is_still_starting` |
| G1.4 | When no session is live, the previous record is kept. | `test_G1_4_record_never_replaces_the_record_with_an_empty_list` |
| G1.5 | A recorded session that is no longer live moves to the "down" list with the time, and the record before each write is kept as `lanes.json.prev`. | `test_G1_5_a_session_that_went_down_moves_to_the_down_list` |
| G1.6 | A token or key is never written to the record or the log. What is kept is the name of the variable that held it and an 8-digit one-way fingerprint (SHA-256, cut short), enough to tell whether a session came back on the same login. | `test_G1_6_a_token_is_never_written_down` |
| G1.8 | A recorded session whose process is there but which Claude Code has not finished loading counts as running. The census shows it as "starting" and `record` keeps it. | `test_G1_8_a_session_that_is_still_starting_is_not_taken_for_down` |
| G1.9 | A session that is not in tmux is recorded to come back in tmux, on the socket named in `loose_socket` (default `tardigrade`), under its own name with `loose_prefix` before it. A session outside tmux whose name matches a pattern in `loose_skip` is not recorded. | `test_G1_9_a_session_outside_tmux_is_recorded_for_the_socket_and_prefix_in_the_settings` |
| G1.10 | With `tsv_path` set, each record is also written as a tab-separated table with eight columns (tmux session, session id, folder, model, effort, permission mode, tab label, workspace label), for tools that read such a table. `tsv_socket` limits it to one tmux socket. One writer per table: the person switches the other writer off. | `test_G1_10_the_record_is_also_written_as_an_eight_column_table_when_asked` |
| G1.11 | When more than half of the recorded sessions (and at least three) vanish between two records, that is a crash, not a person ending them. They stay on the record as sessions to bring back for `lost_keep_h` hours (default 12), and the census says so. `record --force` lets them go. | `test_G1_11_sessions_that_vanish_together_stay_on_the_record` |
| G1.12 | The model recorded is the one the session is using now, read from its conversation with the full model id, not the one it was started with and not the machine's default. | `test_G1_12_the_model_recorded_is_the_one_in_use_not_the_one_it_started_with` |
| G1.7 | Sessions on a tmux socket named in `skip_tmux_sockets` are not recorded, so a second tool that already brings them back stays the only one that does. The census still counts them. | `test_G1_7_sessions_on_a_skipped_tmux_socket_are_left_to_the_other_tool` |

### G2 Revive: bring each one back as itself

| Codon | Claim | Proof |
|---|---|---|
| G2.1 | `revive` starts each recorded session that is not running with `claude --resume <its id>`, in its folder, with its model, effort and permission mode, in a tmux session of the same name on the same socket. | `test_G2_1_revive_brings_a_session_back_as_itself` |
| G2.2 | With `"wake": "note"` (the default) the revived session gets one message: the machine restarted, the conversation is intact, background work is gone, check and carry on. The message is typed into the session once Claude Code reports it loaded and idle. It is never on a command line. | `test_G2_2_the_revived_session_is_told_what_happened` |
| G2.3 | With `"wake": "idle"` nothing is typed and the session waits. | `test_G2_3_wake_idle_types_nothing` |
| G2.4 | A session id held by any live process of this user is never started. | `test_G2_4_never_a_second_copy` |
| G2.5 | `revive --boot` acts only on a machine that has just started, and only once per boot. | `test_G2_5_boot_revive_runs_once_per_boot_and_only_after_a_boot` |
| G2.6 | `--dry-run` says what would start and starts nothing. | `test_G2_6_dry_run_starts_nothing` |
| G2.7 | A revived session gets a clean environment: nothing of the calling session's `CLAUDE*` variables reaches it. | `test_G2_7_a_revived_session_gets_a_clean_environment` |
| G2.8 | Where every session carries the same login token (a machine whose `claude` command or shell supplies it), sessions are brought back as usual, and each one's login is checked against its fingerprint after the start. | `test_G2_8_a_machine_wide_login_token_comes_back_and_is_checked` |
| G2.9 | Two revives never run at the same time. | `test_G2_9_two_revives_at_once_do_not_both_run` |
| G2.10 | `revive --only NAME` brings back that one session and no other. | `test_G2_10_only_brings_back_just_that_one` |
| G2.11 | A session's login is checked before anything is typed into it. Where sessions on one machine have different logins and one comes back on another login than it had, it is listed as "needs you", idle, with no note typed and nothing run on the wrong account. | `test_G2_11_a_pinned_session_comes_back_silent_and_a_changed_login_is_reported` |
| G2.12 | Opt-in, off by default (`trust on`): when a session this run brought back is asked "trust this folder?" for the folder it was recorded in, Tardigrade answers Yes, and says so in the log. It keeps looking for the question for up to 15 minutes after a boot (`watch_min`), because a slow machine asks late. | `test_G2_12_with_trust_on_the_question_is_answered_for_the_sessions_own_folder` |
| G2.13 | The answer is two keys, Down and Enter, sent from one function. Enter is sent only when the screen shows the cursor on "Yes, I trust". | `test_G2_13_enter_is_sent_only_with_the_cursor_on_yes` |
| G2.25 | The trust answer counts only the dialog that is open at the foot of the screen now: both choices in the last 6 lines that have text, and no word about usage or limits in the 15 lines there. A conversation that quotes the question, with another dialog open under it, gets no key at all. The screen is read again right before Enter. | `test_G2_25_no_key_is_sent_when_the_question_is_only_quoted_and_another_dialog_is_open` |
| G2.26 | A tmux server that Tardigrade has to start is started the machine's own way: with the config file in `tmux_conf`, or by the person's own `server_command` (run once, when no server is up on that socket), and from a clean environment plus the `env_file`. | `test_G2_26_the_tmux_server_is_started_the_machines_own_way` |
| G2.27 | An `env_file` never sets the variables that belong to the starting shell (its folder, user id, depth, terminal, tmux pane), and the start file changes to the session's own folder after everything else, so a session starts in its folder whatever the shell's start files do. | `test_G2_27_an_env_file_never_moves_a_session_out_of_its_folder_or_sets_the_shells_own_variables` |
| G2.28 | On a machine with thousands of processes the process list is read once per pass and handed down, not once per session. | `test_G2_28_one_process_list_per_pass_is_taken_and_handed_down` |
| G2.14 | The permission mode comes back word for word. A mode is never made looser: a session that ran with checks on does not come back with checks off, whatever the mode was called. | `test_G2_14_the_permission_mode_comes_back_word_for_word_never_looser` |
| G2.15 | With `account_env` and `account_command` set, a session that carried an account label is started through that command (for example a wrapper that pins it to the account), and no other session is. The label is a name, never a secret. | `test_G2_15_a_session_pinned_to_an_account_starts_through_its_wrapper` |
| G2.16 | With `start_command` set, that line starts each session in place of Tardigrade's own, with the session's id, folder, name, model, effort, mode, account and exact flags put in, each quoted for the shell. | `test_G2_16_a_start_command_from_the_settings_replaces_the_start_line` |
| G2.17 | With `env_file` set, its variables reach each revived session (a desktop's display and session bus, for one). A variable whose name looks like a secret's is left out, and the log names it without its value. | `test_G2_17_an_env_file_reaches_the_revived_session_and_a_secret_in_it_does_not` |
| G2.18 | A settings variable named in `carry_env` (default: the auto-compact window) comes back with the value the session had. A name that looks like a secret's is never recorded, whatever the setting says. | `test_G2_18_a_settings_variable_on_the_carry_list_comes_back_with_its_value` |
| G2.19 | Nothing but the start command is on a command line. The shell that holds a revived session was started with "read this file", and the file (readable by this user only) holds the settings and the start line. Claude Code's own command line is its flags and `--resume <id>`. | `test_G2_19_nothing_but_the_start_command_is_on_a_command_line` |
| G2.20 | The note is typed only into a session that Claude Code reports idle and whose screen shows no question. A session that comes back to a question of its own gets no note, and the log names it. | `test_G2_20_the_note_is_not_typed_into_a_session_that_is_not_idle` |
| G2.21 | `revive --dry-run --all` prints the start line of every recorded session as if none were running, starts nothing and writes no start file. It is the check to run before a planned reboot. | `test_G2_21_dry_run_all_shows_every_start_line_and_starts_nothing` |
| G2.22 | A start that fails is tried again for up to `retry_min` minutes (the network can lag at boot), in the tmux session the first try made, and only while that session holds a bare shell. | `test_G2_22_a_start_that_fails_is_tried_again_in_the_same_tmux_session` |
| G2.23 | With `load_max` set, each start waits (up to `load_wait_s`) while the machine's load is above it. Off by default. | `test_G2_23_with_load_max_set_a_loaded_machine_is_given_time_between_starts` |
| G2.24 | After the starts it keeps looking for up to `watch_min` minutes, then counts: LOADED (Claude Code has written the session's own file), UP-NOT-LOADED and DOWN, the last two by name. "A process is up" is not counted as loaded. | `test_G2_24_after_the_starts_it_counts_loaded_and_names_what_is_not` |

### G3 Census: say what is running and what is not

| Codon | Claim | Proof |
|---|---|---|
| G3.1 | `census` lists three groups: running with a window, running with no window open (hidden), and on the record but not running (down). | `test_G3_1_census_names_hidden_and_down_sessions` |
| G3.2 | `census` changes nothing: it starts nothing and writes nothing to the record. | `test_G3_2_census_changes_nothing` |
| G3.4 | `census` counts the sessions that are stopped at a question or a dialog, and says so in its first lines. | `test_G3_4_census_counts_sessions_that_wait_for_an_answer` |
| G3.5 | A recorded session that has stopped at Claude Code's "trust this folder?" question is shown as `trust?` and counted as waiting. Tardigrade reads the pane to find this out and keeps nothing from it. | `test_G3_5_census_names_a_session_parked_at_the_trust_question` |
| G3.3 | On a shared machine, other users' Claude processes are counted by program name only. Their arguments, environment and files are never read. | Not verified by a test (see below). |

### G4 Windows and tabs

| Codon | Claim | Proof |
|---|---|---|
| G4.1 | `open` opens a terminal window for each hidden session. Where it cannot open one, it prints the line to type instead. | `test_G4_1_open_prints_the_attach_line_when_it_cannot_open_a_window` |
| G4.2 | With Herdr, a revived session is attached again in the tab that carries its label, or in a new tab in its workspace when the tab is gone. The only thing typed into a Herdr pane is the `tmux attach` for that session, and only when the pane holds an idle shell with nothing typed after its prompt. A tab that runs something else, or has typed text, is left alone. | `test_G4_2_a_herdr_tab_gets_its_session_back_only_at_an_idle_shell_with_an_empty_prompt` (with a stand-in for the `herdr` command) |

### G5 Setup

| Codon | Claim | Proof |
|---|---|---|
| G5.1 | `setup` installs a boot service that runs `revive --boot` 45 seconds after start and leaves the tmux server running after it ends, and a timer that records every 5 minutes. | `test_G5_1_the_boot_service_revives_once_and_outlives_itself` |
| G5.2 | `setup --dry-run` installs nothing. | `test_G5_2_setup_dry_run_installs_nothing` |
| G5.3 | `setup` and the census say who has to log in after a restart. On Linux: whether lingering is on. On a Mac: whether FileVault is on, whether the Mac logs in by itself and as whom, and that the boot agent runs only when this user is logged in at the screen (an ssh login does not start it). Neither is ever changed. | `test_G5_3_setup_and_census_say_who_has_to_log_in_on_a_mac` (the wording, from made-up facts) |
| G5.5 | `trust on` and `trust off` write the setting, and each says in plain words what Tardigrade will and will not answer. With no word it says which it is. | `test_G5_5_trust_on_and_off_write_the_setting_and_say_what_it_means` |
| G5.4 | `uninstall` removes the service and the timer and touches no running session and no record. | Not verified by a test. |

## Invariants

These hold across every gene. A change that breaks one is a plan change first.

| Invariant | Proof |
|---|---|
| INV1 It never ends a Claude session and never ends a tmux session. The one thing it replaces is the leftover shell in a tmux session it made itself, in the same run, when the first start failed. | `test_INV1_it_never_ends_a_session` |
| INV2 It types two things only, each from one function, and only into a session it started itself in this run. One: its own wake-up note, once the session is loaded and idle with no question on its screen (G2.2, G2.20); `"wake": "idle"` switches it off. Two, which only the person can switch on: the trust answer (G2.12, G2.13). It answers no other dialog. Into a Herdr pane it types only the `tmux attach` of G4.2. | `test_INV2_it_types_two_things_only_each_from_one_function` |
| INV3 It never stores a secret. A login token leaves only a variable name and an 8-digit fingerprint. A variable whose name looks like a secret's is never recorded, carried or copied into a start file. | G1.6, G2.17, G2.18 |
| INV4 Never a second copy of a session. | G2.4, G2.9 |
| INV5 It reads only this user's processes. | G3.3 |
| INV6 Python standard library only, and no network. | `test_INV6_standard_library_only_and_no_network` |

The link between this plan and the tests is itself tested: every proof named here is a real test, and every codon
test has a codon here.

## Proven by hand on real machines, not by a test

- 2026-11-21, an Ubuntu 24.04 laptop, Claude Code 2.1.323, **two real reboots** (one clean, one a sudden reset with
  no shutdown). Setup said "boot service: enabled, record timer: active, lingering is on". Three sessions (permission
  checks on; permission checks off; one in a plain terminal window) came back both times with the same ids, names
  and folders: all up 2 min 48 s and 2 min 47 s after boot. The boot service started 88 to 90 seconds after boot,
  before any remote login. The record survived the reset. The first record after each boot waited for the revive
  to finish (G1.3). A second restore tool brought back a fourth session at the same second; with
  `skip_tmux_sockets` neither touched the other's (G1.7). This proves G5.1 on Linux.
- Same laptop, a fourth reboot on 0.1.6 with `trust on`: four sessions back, the one in the home folder asked the
  trust question and answered Yes by Tardigrade 5 seconds after its start, logged, then loaded; "0 still not
  loaded". The usage-limit question that followed was left alone (G2.12, G2.13).
- Same laptop: `open` on its real desktop opened three windows, and the census then showed no hidden session (G4.1).
- 2026-11-21, an Intel Mac, macOS 13, auto-login on, FileVault off, Claude Code 2.1.330, **one real reboot** on
  0.1.3: the launchd agent ran 166 seconds after boot and brought back two sessions with the same ids and names.
  On that Mac the `claude` command supplies the login token, and the login check passed (G2.8). The sessions
  needed about two more minutes to finish loading. The reboot before it, on 0.1.2, brought back 0 of 2: the
  reason for plan 1.3. This proves G5.1 on macOS for a Mac that logs in by itself.
- 2026-11-21, Linux, Claude Code 2.1.330, a real session: started in tmux, recorded, ended, then brought back with
  `revive --only`. Same session id, name, folder and model, its earlier messages on screen, and it acted on the
  note (G2.2).
- 2026-11-21, a shared Linux machine with 98 sessions: `census` took a quarter of a second, found 98 running and 2
  hidden, and its 98 session ids matched a separate tool's list, 98 of 98.

## Not verified

- **A session acting on the note after a real reboot.** On the test laptop the account was at its usage limit, so
  each revived session stopped at Claude Code's "limit reached" question. Tardigrade answered nothing (INV2) and the
  census counted them as waiting (G3.4).
- **Nobody logged in at all.** The laptop logs into its desktop by itself, so lingering alone was not isolated.
- **macOS with FileVault on, or without auto-login.** Setup says what will happen (G5.3); no such Mac was rebooted. `open` was not run on a Mac.
- **Herdr on a real machine** (G4.2). The test uses a stand-in for the `herdr` command, so it proves the rule
  (what is typed, and when not) and not Herdr itself.
- **0.1.7 on a real reboot.** The start file (G2.19) and the typed note (G2.2) are a new start path. Everything
  under "Proven by hand" above was done on 0.1.6 or earlier and has to be done again.
- **A machine with about 100 sessions** through a real reboot. The most brought back at once so far is four.
- **G3.3 and INV5.** The check is one line (`uid != me` before anything is read), read by a person, not exercised
  by a test, because a test cannot easily start a process as another user.
- **Sessions pinned to different accounts by a token** (G2.11) are proven with the stand-in only, not on a real
  machine with two accounts.
- **G5.4** has no test. **G5.3** on a Mac is tested as wording from made-up facts; a Mac with FileVault on, or one
  that logs in as another user, was not rebooted.
- **Windows.** Not supported.

## Not goals

- It does not restore files, git state, or anything a session was in the middle of writing.
- It does not restart a session that ended while the machine stayed up. `census` shows it as down, and
  `revive --only` brings it back when you ask.
- It does not bring back timers, watchers or background commands. It tells the session they are gone.
- It does not manage other users' sessions.
