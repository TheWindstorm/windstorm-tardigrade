# DNA Strand Master Plan: Windstorm Tardigrade

Plan version 1.8 (2026-11-22). Builds: `scripts/tardigrade.py` 0.1.8, being built. Until every promise due in
0.1.8 has its test, the release is 0.1.7.
What is promised for the versions after it is under "Planned" below and in `PROMISES.json`.

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

**The first reboot with about a hundred sessions** (2026-11-21, a shared machine, 98 sessions, version 0.1.7).
It worked and it was not good enough, and both halves are lessons.

- What held: all 98 came back by themselves, none failed, none needed a retry, each with its own mode, model and
  folder, through the machine's own way of starting its tmux server. Loaded and counted 25 and a half minutes
  after boot. So the rules above are right, and they stay.
- It was slow. One session at a time, about 11 seconds each, most of it waiting: 18 minutes of starts for 98.
  So: several at a time, with the decision to start still made one by one (G2.34).
- It was silent. Tabs were attached in one block at the end, and nothing said that sessions were on their way.
  The machine's owner looked 20 minutes after boot, saw an empty shell where his session should have been, and
  started a second copy by hand. The skill's first rule is "never a second copy", and here its own silence caused
  one. So: each tab as its session comes up (G4.3), and the place a session will appear says it is coming
  (G4.4, G3.6).
- A layout can be gone. Five Herdr workspaces did not come back, and six sessions had no tab until a person made
  the workspaces again. So: make the workspace again under its old name (G4.5).
- A name is not an identity. The hand-started copy carried the real session's name; `revive --only NAME` matched
  both and started the copy its owner had just closed. So: refuse an ambiguous name (G2.35), do not bring back
  what its owner ended (G2.36), and let the census flag both (G3.7).
- The story of a restore should not need a log reader (G3.8), and switching a machine over should not need labels
  copied by hand (G5.6).
- Two things a second kind of user needs, seen the same night: windows that open by themselves after login (G4.6),
  and a way to see who is busy before a planned reboot (G3.9).
- Why the boot run began 4 minutes 35 seconds after boot on that machine and after about 2 minutes on a laptop
  (looked into for plan 1.8, from that boot's own journal, read only). Uptime counts from the start of the kernel.
  The kernel took 1 min 30 s. Then the machine's own start held every user service back: one of its system services
  took 2 min 17 s, and the per-user service manager could start only after it, 3 min 50 s after boot. Then the boot
  service's own fixed wait, 45 s. So 45 s of the 4 min 35 s are ours, and the rest is that machine's start. So: the
  boot run says when it began and what it waited for (G5.8), its own wait becomes a setting, `boot_wait_s`, still 45 s
  by default until a machine has shown a shorter one is safe, and `report` (G3.8) shows the parts.
- From the agent that runs that machine, who did by hand what the skill did not do. Nobody woke the sessions: all
  98 came back idle, as that machine was set, their watchers were gone, and 70 minutes later only 9 had done
  anything (G2.37). `/tmp` is emptied by a reboot, and tools some sessions relied on lived there (G2.37, G3.10).
  The count reached nobody (G2.38). Retiring the older tool meant finding every caller by hand (G5.7). A workspace
  made again comes with a blank tab (G4.7). `revive --only` did not say that it had attached a tab (G4.8). A
  session started by hand outside the record was not flagged as unprotected (G3.12). A second user's result could
  only be counted from outside (G3.13). Some of that user's own services and timers behaved differently after the
  reboot (G3.11). A start flag was dropped without a word (G2.39). And the whole round of asking sessions before a
  reboot and reminding them after it was done by hand (G6.1).

A lesson is easy to write down and easy to leave behind. Each of these is a row in `PROMISES.json` with the
version it is due in, and the tests refuse that version while the row is not built (see "Promises" below).

## Genes and codons

### G1 Record: write down what is running, all the time

| Codon | Claim | Proof |
|---|---|---|
| G1.1 | `record` writes one row per live session of this user: session id, name, folder, model, effort, permission mode, and the tmux socket and session it lives in. | `test_G1_1_record_writes_down_each_live_session` |
| G1.2 | A session started by another session, or one that is not interactive, is not recorded. | `test_G1_2_record_skips_helper_sessions` |
| G1.14 | A one-shot run is never a lane, wherever it runs: `claude -p` (which Claude Code marks "interactive" in its status file), a run through an SDK, a process whose input is not a terminal, and a process started inside another Claude session (a claude among its parents below the tmux pane, Herdr, terminal window, editor or ssh login it lives in; a claude above that place does not count). It is not recorded, listed or started again. | `test_G1_14_a_one_shot_run_is_never_a_lane_wherever_it_runs` |
| G1.15 | A setting names folders to leave alone (`skip_cwd_prefix`): a claude process working inside one (its folder, from the system) is counted, and Tardigrade's own code reads only its folder: not its environment, its arguments, its status file or its conversation. On Linux, the system's `ps`, which Tardigrade runs to list processes, opens more than that for every process on the machine (its environment and arguments too; seen with strace, procps-ng 4.0.4) and passes back only process id, parent, user, state and program name. From 0.1.10 the list is read from /proc directly (T47). It is never recorded; census says how many were left alone; revive never starts a row in such a folder, one recorded before the setting included, since it cannot see whether that session still runs (T41). | `test_G1_15_a_process_in_a_folder_to_leave_alone_has_only_its_folder_read` |
| G1.16 | Due in 0.1.10 (T47). On Linux the process list is read from `/proc` directly (each process's `stat`, its owner from the entry itself), not from `ps`: nothing opens another process's environment or arguments before it is known to be one of this user's sessions, and a process in a `skip_cwd_prefix` folder has only its folder read. macOS keeps `ps`. On every box the new build names exactly the same live sessions as the old one. | Not built yet. |
| G1.17 | Due in 0.1.10 (T54). A running session whose own session file exists but cannot be read keeps its previous row on the record, as it was, marked "could not be read at" a time; the rest of the record is written. A read that failed is never taken as "gone". | Not built yet. |
| G1.18 | (0.1.9) A failed read of the process list or of a tmux server's panes (any failure but tmux's own "no server running", or no answer within 6 s) leaves the record exactly as it was, says why on every pass, and census names a skip that has lasted an hour: each is one shared read, so when it fails nothing is known about any session or its place. Only the sockets the record depends on count (each recorded session's, tmux's default, `loose_socket`, `tsv_socket`): a wedged server elsewhere does not stop the record. A socket left by a server that is gone (after a reboot) is "no panes there", as before (T55). | `test_G1_18_a_failed_pane_or_process_read_leaves_the_record_as_it_was` |
| G1.19 | Due in 0.1.10 (T56). Which process holds which session is decided on every install of Claude Code, also one installed through npm at 2.1.112 or older (run by node: first named after the runtime, then renamed with its start arguments rewritten, until its session file is written). A process that cannot yet be told apart is "cannot tell" for the session in its folder only. | Not built yet. |
| G1.3 | While the machine is still starting, and (once setup has run) until this boot's revive has finished, `record` writes nothing. | `test_G1_3_record_waits_while_the_machine_is_still_starting` |
| G1.4 | When no session is live, the previous record is kept. | `test_G1_4_record_never_replaces_the_record_with_an_empty_list` |
| G1.5 | A recorded session that is no longer live moves to the "down" list with the time, and the record before each write is kept as `lanes.json.prev`. | `test_G1_5_a_session_that_went_down_moves_to_the_down_list` |
| G1.6 | A token or key is never written to the record or the log. What is kept is the name of the variable that held it and an 8-digit one-way fingerprint (SHA-256, cut short), enough to tell whether a session came back on the same login. | `test_G1_6_a_token_is_never_written_down` |
| G1.8 | A recorded session whose process is there but which Claude Code has not finished loading counts as running. The census shows it as "starting" and `record` keeps it. | `test_G1_8_a_session_that_is_still_starting_is_not_taken_for_down` |
| G1.9 | A session that is not in tmux is recorded to come back in tmux, on the socket named in `loose_socket` (default `tardigrade`), under its own name with `loose_prefix` before it. A session outside tmux whose name matches a pattern in `loose_skip` is not recorded. | `test_G1_9_a_session_outside_tmux_is_recorded_for_the_socket_and_prefix_in_the_settings` |
| G1.10 | With `tsv_path` set, each record is also written as a tab-separated table with eight columns (tmux session, session id, folder, model, effort, permission mode, tab label, workspace label), for tools that read such a table. `tsv_socket` limits it to one tmux socket. One writer per table: the person switches the other writer off. | `test_G1_10_the_record_is_also_written_as_an_eight_column_table_when_asked` |
| G1.11 | When more than half of the recorded sessions (and at least three) vanish between two records, that is a crash, not a person ending them. They stay on the record as sessions to bring back for `lost_keep_h` hours (default 12), and the census says so. `record --force` lets them go. | `test_G1_11_sessions_that_vanish_together_stay_on_the_record` |
| G1.12 | The model recorded is the one the session is using now, read from its conversation with the full model id, not the one it was started with and not the machine's default. | `test_G1_12_the_model_recorded_is_the_one_in_use_not_the_one_it_started_with` |
| G1.13 | A recorded session whose own tmux session now holds another session was replaced there. It leaves the record and is not listed as down. | `test_G1_13_a_session_replaced_in_its_own_tmux_session_is_not_listed_as_down` |
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
| G2.35 | `revive --only WORD` counts the recorded sessions whose name, tmux name or id is WORD. When more than one matches, it starts nothing, lists each with its id, folder and whether it runs, and says to give the id instead. | `test_G2_35_only_refuses_a_name_that_two_sessions_carry` |
| G2.36 | A session its owner ended is not brought back. It is ended when the last prompt typed into it is exactly `/exit` (or `/quit`), in Claude Code's prompt history or as its conversation's last entry, written after a record last saw it running, and no process holds it now; or, for a session that went alone, when its tmux pane holds the shell it left. This is read when it matters (by `revive`, `census` and `record`), so it needs no daemon: `revive` and the boot run skip it, `census` lists it as ended, and `record` puts it on the down list at once. Only `/exit` takes a session out of the crash count of G1.11: many sessions gone at once with their shells left (a memory kill, say) are still a crash. `revive --only <its id>` still brings it back, because then a person asked. The prompt history holds what people typed: only lines of session ids on the record are read, only their time and whether they are exactly `/exit` or `/quit` are kept, and nothing of it is logged, recorded or shown. A history that is missing or not as expected ends nobody, and the log says the check could not be made. | `test_G2_36_a_session_its_owner_ended_is_not_brought_back_and_a_crash_is_still_a_crash`, `test_G2_36_an_exit_that_only_the_prompt_history_has_still_counts` |
| G2.37 | The wake-up note per session: names matching a `wake_skip` pattern never get it, names matching a `wake_names` pattern get it even with `"wake": "idle"`. Its words can be set (`wake_text`, with `{when}`). The default words say that /tmp was emptied when nothing in /tmp is older than this boot. At most `notes_per_min` notes (default 6) are typed in any one minute, so on a machine with many sessions they do not all take a turn at once, and the watch lasts long enough for each. A note is typed only into a session Claude Code reports idle, with no question at the foot of its screen, and with its input box found and empty (a dim placeholder in it, as a session nobody wrote in shows, counts as empty; typed text does not). | `test_G2_37_the_note_per_session_in_its_own_words_spaced_and_only_into_an_empty_box` |
| G2.39 | A session comes back with the start flags it had, all of them except the ones that name a conversation or pass a first message. A flag that cannot be carried is named in the log when the session is recorded, never dropped silently, and the README lists what is and is not carried. Never carried: a value that names a secret or has the shape of a common key or token, and inline JSON for `--settings`, `--mcp-config` or `--agents` (a file's path is carried). `--remote-control` is carried, and the log says so. | `test_G2_39_every_start_flag_comes_back_and_none_is_dropped_silently` |
| G2.42 | The record, the table at `tsv_path`, the progress file, the boot markers (only the newest five kept, T38) and the log are this user's only (0600), and Tardigrade's own folders too (0700), whatever mode they had: they hold start flags word for word, and a home folder is not always closed (a Mac's is 755). | `test_G2_42_the_record_the_table_and_the_log_are_this_users_only`, `test_G2_42_only_the_newest_few_boot_markers_are_kept_and_they_are_private` |
| G2.43 | A session that goes down while being watched, whose own pane, in a tmux session this run made, says "No conversation found", is started new once, with the same name, folder and flags, as G2.30 does, and counted again; the counts do not keep it as started or "needs you". A start that fetches a login first can say it only after the settle look. | `test_G2_43_a_session_slow_to_say_it_has_no_conversation_is_still_started_new` |
| G2.44 | A session that was started but not yet seen up at its settle look is looked at in every watch pass, not only at the next retry round (`retry_gap_s`, default 60 s), so a start that is a little late (the first network call after boot, for a login) is counted and looked after at once: trust answer, note, tab. | `test_G2_44_a_start_that_is_up_a_little_late_is_looked_after_at_once` |
| G2.40 | No wake-up note is typed into a session whose screen shows a usage or limit notice in the 15 lines at its foot, whatever its form ("Usage limit reached", "/usage-credits", "hit your limit", a weekly or 5-hour limit, and the like), even under an empty input box. The log says that this is why. The same pattern holds back the trust answer (G2.12). "The foot" is the 15 lines above whatever sits under the input box's lower edge (notices, a status line of any height, the mode line), plus those lines, up to 40; the input box, a question and a queued note are looked for there too (T36). | `test_G2_40_no_note_is_typed_beside_a_usage_or_limit_notice`, `test_G2_40_a_tall_status_line_moves_where_the_box_is_looked_for`, `test_G2_40_a_notice_above_a_tall_status_line_still_holds_the_note` |
| G2.45 | No wake-up note is typed into a session whose conversation last ended on a usage or limit reply (Claude Code's assistant line marked as an error): Claude Code would hold it and run it by itself at the reset. The log names the reply. After each note the screen is looked at once more; a note held for the reset is named in the log and in the counts ("note queued until the reset: N"), and no key is pressed to drop it. A limit belongs to the login: once a note typed in this run meets it (limit words that appear on that screen after the note and were not there before; never an old limit reply in a conversation, nor one on a screen after a resume, which shows history: each holds its own session's note only), no other session on the same login (account label, login fingerprint and Claude Code folder) gets a note in that run, and the count names them. A `wake_text` whose words read as a limit notice is refused: `setup` names the words and every run types the default note instead. Not covered: a session at its limit that has not met it in its own conversation still gets the note; the second look only says so. | `test_G2_45_no_note_into_a_conversation_that_ended_on_a_limit_and_a_note_held_for_the_reset_is_named`, `test_G2_45_an_old_limit_reply_on_a_screen_holds_that_session_only`, `test_G2_45_a_note_whose_words_read_as_a_limit_notice_is_refused`, `test_G2_45_once_one_note_meets_the_limit_no_other_session_on_that_login_gets_one` |
| G2.46 | (0.1.10) A session being moved between logins at the moment of a restart (its /exit, then a resume on another login seconds later) is not left down without a word: census names it with the command that brings it back. | (planned) |
| G2.47 | Revive never starts a row whose conversation last names a print-mode or SDK entrypoint ("sdk-cli"; a session a person sits in says "cli", and a print-mode conversation someone resumed by hand later is a session again). It says so in the log, `--only` refuses it, census marks it "a print-mode run (claude -p), never brought back", and the record moves it to the down list, also when it finds nothing live and keeps the record before it: a row 0.1.8 wrote for a `claude -p` run does not survive an upgrade (T43). | `test_G2_47_a_row_whose_conversation_is_a_print_mode_run_is_never_started` |
| G2.48 | (0.1.9) A limit reply holds the wake-up note (G2.45) only while its reset is still ahead. The reset moment comes from the reply's own timestamp and the zone it names ("resets 12pm (Zone)" is the first 12pm there after the reply; "resets Nov 24, 6pm (Zone)" is that date), never from today's clock alone. Once it has passed, with a few minutes' margin, the note is typed and the log says so. A reply whose reset cannot be read stays held (T45). | `test_G2_48_a_limit_reply_holds_the_note_only_while_its_reset_is_ahead` |
| G2.49 | (0.1.8.2) A session started at boot gets the same clean environment whether its pane is new or a waiting screen being replaced (tmux gives a respawned pane the `PATH` of the client that asks), so `claude` is found wherever its own installer put it: through `path` exactly as written, or, with no `path`, the default (a standard `PATH` plus `~/.local/bin`) first and then every whole folder of the asking process's own `PATH` that the default lacks, once each, so a pane never has less than before (T48). | `test_G2_49_a_start_finds_claude_by_the_path_setting_not_by_the_boot_services_path` |
| G2.50 | (0.1.9) A restore decides each start on a read of the process list taken right before it, after any load wait. A read that failed (ps gave an error or no answer) is "cannot tell": nothing is started in that pass, before the first start, between starts or at a retry pass; the log says so in plain words and the next pass reads again; after `retry_min` of failed first reads the run ends having started nothing, and this boot's report and `on_done` say so. A session already running is never started a second time on a failed or stale read (T52). | `test_G2_50_a_restore_that_cannot_read_the_process_list_starts_nothing_on_it`, `test_G2_50_a_restore_whose_first_read_keeps_failing_starts_nothing_and_reports_it`, `test_G2_50_a_start_is_decided_on_a_read_taken_after_the_load_wait` |
| G2.51 | Due in 0.1.10 (T57). A session recorded with an account comes back on that account or not at all, and census names it; it never comes back on a different login in silence. A later record never drops a row's account without a log line saying so. | Not built yet. |
| G2.38 | With `on_done` set, that command is run once when a restore ends, with the counts (started, loaded, not loaded, down, failed) in its `{placeholders}` and in `TARDIGRADE_*` variables, so a person or a program is told instead of having to look. It runs from a clean environment plus the `env_file`, and its exit code is logged. | `test_G2_38_on_done_is_run_with_the_counts_when_a_restore_ends` |
| G2.11 | A session's login is checked before anything is typed into it. Where sessions on one machine have different logins and one comes back on another login than it had, it is listed as "needs you", idle, with no note typed and nothing run on the wrong account. | `test_G2_11_a_pinned_session_comes_back_silent_and_a_changed_login_is_reported` |
| G2.12 | Opt-in, off by default (`trust on`): when a session this run brought back is asked "trust this folder?" for the folder it was recorded in, Tardigrade answers Yes, and says so in the log. It keeps looking for the question for up to 15 minutes after a boot (`watch_min`), because a slow machine asks late. | `test_G2_12_with_trust_on_the_question_is_answered_for_the_sessions_own_folder` |
| G2.13 | The answer is two keys, Down and Enter, sent from one function. Enter is sent only when the screen shows the cursor on "Yes, I trust". | `test_G2_13_enter_is_sent_only_with_the_cursor_on_yes` |
| G2.25 | The trust answer counts only the dialog that is open at the foot of the screen now: both choices in the last 6 lines that have text, and no word about usage or limits in the 15 lines there. A conversation that quotes the question, with another dialog open under it, gets no key at all. The screen is read again right before Enter. | `test_G2_25_no_key_is_sent_when_the_question_is_only_quoted_and_another_dialog_is_open` |
| G2.26 | A tmux server that Tardigrade has to start is started the machine's own way: with the config file in `tmux_conf`, or by the person's own `server_command` (run once, when no server is up on that socket), and from a clean environment plus the `env_file`. | `test_G2_26_the_tmux_server_is_started_the_machines_own_way` |
| G2.27 | An `env_file` never sets the variables that belong to the starting shell (its folder, user id, depth, terminal, tmux pane), and the start file changes to the session's own folder after everything else, so a session starts in its folder whatever the shell's start files do. | `test_G2_27_an_env_file_never_moves_a_session_out_of_its_folder_or_sets_the_shells_own_variables` |
| G2.28 | On a machine with thousands of processes the process list is read once per pass and handed down, not once per session. | `test_G2_28_one_process_list_per_pass_is_taken_and_handed_down` |
| G2.29 | The sessions that are up are looked after (trust answer, note, Herdr tab) in every pass, also while another session's start is still being retried. | `test_G2_29_sessions_that_are_up_are_looked_after_while_another_start_is_still_retried` |
| G2.30 | Claude Code keeps no conversation for a session nobody ever wrote in, so it cannot be resumed. When Claude Code says so, the session comes back as a new one with the same name, folder and flags, is not retried as itself, and gets no note. | `test_G2_30_a_session_nobody_ever_wrote_in_comes_back_new_under_the_same_name` |
| G2.31 | The lock that keeps two revives apart says which boot and which process took it. A lock left by a revive that a reboot or a power cut cut off is taken over, so a second restart soon after the first still brings the sessions back. | `test_G2_31_a_lock_left_by_a_revive_that_a_reboot_cut_off_does_not_block_the_next_one` |
| G2.32 | With many sessions the starts take minutes. The sessions already up are looked after during the starts too (at most every 15 seconds), so the first one does not wait at a question until the last one has started. Herdr tabs are done once, when every session is there. | `test_G2_32_a_question_is_answered_while_later_sessions_are_still_being_started` |
| G2.33 | An `env_file` is written while a desktop is open, and after a reboot that desktop is gone. A local display whose socket is missing is not handed to a session. A session bus whose socket is missing is replaced by the user's own bus when that exists, and left out otherwise. | `test_G2_33_a_display_or_session_bus_that_is_gone_is_not_handed_to_a_session` |
| G2.34 | Sessions are started several at a time: at most `starts_at_once` (default 4) started and not yet seen up. The decision to start is still made one session at a time, right before its start, with a fresh process list, the check that no live process holds its id (G2.4) and the check that its tmux session holds nothing of anyone else's. Only the wait after a start (`settle_s`) overlaps with the next starts. `stagger_s` and `load_max` still pace each start, `load_max` read again before each one. A run whose record holds two rows with one session id starts neither and says so. | `test_G2_34_several_starts_at_once_each_decided_alone` |
| G2.14 | The permission mode comes back word for word. A mode is never made looser: a session that ran with checks on does not come back with checks off, whatever the mode was called. | `test_G2_14_the_permission_mode_comes_back_word_for_word_never_looser` |
| G2.15 | With `account_env` and `account_command` set, a session that carried an account label is started through that command (for example a wrapper that pins it to the account), and no other session is. The label is a name, never a secret. | `test_G2_15_a_session_pinned_to_an_account_starts_through_its_wrapper` |
| G2.41 | A revived session that had an account label gets it back from its start file, which sets the `account_env` variable to the label before the start command runs; a session with no label has that variable unset. A wrapper that fetches a login therefore need not put the label back, and the next record still knows the session is pinned. | `test_G2_41_the_account_label_is_set_by_the_start_file_not_left_to_the_wrapper` |
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
| G3.6 | While a restore runs, the first lines of `census` say so: how many of how many are back, and since when. (0.1.9, T37) The record is skipped only while the restore is starting sessions; once every start is done, it runs again whatever the restore still waits for (loading, notes, tabs), and census no longer says not to start any by hand. A desktop note, and a Herdr notification where Herdr is up, says when a restore starts and when it ends. | `test_G3_6_census_and_a_note_say_that_a_restore_is_running`, `test_G3_6_the_record_runs_again_once_every_start_is_done`, `test_G3_6_a_session_that_dies_in_the_watch_is_written_as_it_would_be_at_any_other_time` |
| G3.7 | `census` lists every name that more than one session carries, running or on the record, with each id. A running session that started after the last restore began, carrying the name of a session that restore was bringing back under another id, is marked as started by hand while its session was coming back. | `test_G3_7_census_names_a_name_two_sessions_carry_and_a_copy_started_by_hand` |
| G3.12 | `census` marks each running session that is not on the record and says why (a pattern in the settings leaves it out, it is a helper of another session, it is newer than the last record), because such a session does not come back. | `test_G3_12_census_names_each_running_session_that_will_not_come_back_and_why` |
| G3.16 | `census` never lists as DOWN a session whose own tmux session runs its replacement under the same name (a session started new under a new id because Claude Code had no conversation for it): it lists it as started new, with the old and the new id, until the next record takes the new id. The old id does not count toward a shared name (G3.7), and the new id is not listed as "will not come back" (G3.12). | `test_G3_16_a_session_started_new_under_a_new_id_is_not_listed_as_down` |
| G3.17 | (0.1.9) A session that goes down during a restore stands out for that boot: census and `report` say "down during the restore: NAME (at HH:MM)", whether it went before the count or after it while the run still waits. On a later boot it is an ordinary down row (T46). | `test_G3_17_a_session_that_goes_down_during_a_restore_stands_out_for_that_boot` |
| G3.18 | (0.1.9) On a failed read of the process list or of a tmux server's panes, census (text and `--json`) says it cannot tell and gives no count of running or down sessions; `report` says the same, and a restore's count and `on_done` give "unknown" instead of a number (T53). | `test_G3_18_census_on_a_failed_read_says_it_cannot_tell` |
| G3.8 | `report` prints the last restore's timeline from times the run itself wrote down: the machine's start, the run's start and its own wait, the first and the last start, how many were up and loaded and when, the tabs attached and not (and why), and what still needs a person (failed, needs you, not loaded, note not typed, waiting at a question, ended by its owner, a name carried twice). Local time. It reads only. | `test_G3_8_report_prints_the_last_restores_timeline_and_what_needs_a_person` |
| G3.3 | On a shared machine, other users' Claude processes are counted by program name only. Tardigrade's own code never reads their arguments, environment or files (on Linux the system's `ps` it runs opens more of every process, until 0.1.10: see G1.15). | Not verified by a test (see below). |

### G4 Windows and tabs

| Codon | Claim | Proof |
|---|---|---|
| G4.1 | `open` opens a terminal window for each hidden session. Where it cannot open one, it prints the line to type instead. | `test_G4_1_open_prints_the_attach_line_when_it_cannot_open_a_window` |
| G4.2 | With Herdr, a revived session is attached again in the tab that carries its label, or in a new tab in its workspace when the tab is gone. The only thing typed into a Herdr pane is the `tmux attach` for that session, and only when the pane holds an idle shell with nothing typed after its prompt. A tab that runs something else, or has typed text, is left alone. | `test_G4_2_a_herdr_tab_gets_its_session_back_only_at_an_idle_shell_with_an_empty_prompt` (with a stand-in for the `herdr` command) |
| G4.3 | With Herdr, a session's tab is attached as soon as its tmux session exists, which with G4.4 is at the start of the run, and looked at again in every pass, in the same pass as the trust answer and the note. A tab that could not be attached in one pass is tried in the next; the end of the run is a last try, not the only one. What is typed and when (G4.2) does not change. | `test_G4_3_each_tab_is_attached_at_the_start_not_after_the_last_start` |
| G4.4 | At the start of a run, each session it will start gets its own tmux session at once, holding a waiting screen: that Windstorm Tardigrade is bringing this session back, how many of how many are back, and that it will appear in this place. When its turn comes, its Claude replaces the waiting screen in the same pane. So a person who opens its tab, its tmux session or an `open` window at any moment sees that it is coming. The waiting screen is a small program, not a shell: it echoes nothing, runs nothing typed into it, ignores Ctrl+C, hangup and terminate, and shows no prompt-like mark (no `❯`, and no `$`, `%` or `#` at a line end), so a waker that types into idle panes finds nothing to type into. Its pane title is the session's name with "(coming back)" after it. A tmux option on the session marks it as waiting, so a later run, `census` and every count read it as coming back, never as running: up and loaded are judged by the Claude process and its session file, never by a tmux session existing. A `server_command` runs, and its server is up, before the first waiting screen. When its turn comes, Claude replaces the waiting screen in the same pane through the same start file and folder as any start, with nothing of the run's own environment, and the pane's history is cleared. Right before that, the fresh check of G2.34 looks at the session ids live processes hold, not at tmux names: a session that is running somewhere else by its turn is not started, and its waiting screen says where it runs, then ends and removes its own tmux session. A waiting screen that is gone by its turn is started the usual way, and its tab attached again. While a run is going (its lock held by a live process of this boot), `record` writes nothing; a run that died does not stop the record. | `test_G4_4_a_waiting_screen_holds_each_place_until_its_session_comes` |
| G4.5 | With Herdr, a session whose workspace is gone gets its workspace made again under its old label, once per run for all the sessions that belong in it, never focused (no tab it makes is focused either, so a run never moves the person's view), and its tab in it under its old label. Only if the workspace cannot be made is `herdr_default_workspace` used, when it is set. The log says which. | `test_G4_5_a_workspace_that_is_gone_is_made_again_under_its_old_name` |
| G4.7 | When a workspace is made again (G4.5), the tab it comes with is used for the first session placed there, renamed to that session's label, so no blank tab is left. Its shell may still be starting, so it gets up to 10 s to show an idle prompt (T34). A first tab that is not blank by then (anything running in it, or text typed after its prompt) is left as it is, and a new tab is made. Tardigrade closes no tab. | `test_G4_7_the_tab_a_workspace_made_again_comes_with_is_used_not_left_blank`, `test_G4_7_a_new_workspace_tab_whose_shell_is_still_starting_is_waited_for` |
| G4.9 | A restore that stopped leaves nothing unexplained. A start that gave up says in the log what its tmux session holds now: a shell, its waiting screen (which then says the restore stopped and how to go on), or nothing. `census` counts waiting screens left by a restore that no longer runs as "waiting, stopped", apart from those still coming back, and says to run `tardigrade revive`. | `test_G4_9_after_a_restore_stopped_census_counts_its_waiting_screens_as_stopped` |
| G4.10 | (0.1.9) A restore whose record has tabs says so when Herdr is not running: at the first pass the log says "Herdr is not running: N tab(s) wait for it (up to M minutes)", and census, `report` and `on_done` say "Herdr is not running: N tab(s) not attached". The tabs are still attached when Herdr appears (G4.3), and the last pass still says when it never came. Tardigrade never starts Herdr itself (T44). | `test_G4_10_a_restore_says_at_once_when_herdr_is_not_running`, `test_G4_10_when_herdr_never_comes_census_report_and_on_done_say_how_many_tabs_wait` |
| G4.8 | `revive --only` says what it did about the session's tab: attached, already there, or left alone and why. | `test_G4_8_revive_only_says_what_it_did_about_the_tab` |

### G5 Setup

| Codon | Claim | Proof |
|---|---|---|
| G5.1 | `setup` installs a boot service that runs `revive --boot` 45 seconds after start and leaves the tmux server running after it ends, and a timer that records every 5 minutes. | `test_G5_1_the_boot_service_revives_once_and_outlives_itself` |
| G5.2 | `setup --dry-run` installs nothing. | `test_G5_2_setup_dry_run_installs_nothing` |
| G5.3 | `setup` and the census say who has to log in after a restart. On Linux: whether lingering is on. On a Mac: whether FileVault is on, whether the Mac logs in by itself and as whom, and that the boot agent runs only when this user is logged in at the screen (an ssh login does not start it). Neither is ever changed. | `test_G5_3_setup_and_census_say_who_has_to_log_in_on_a_mac` (the wording, from made-up facts) |
| G5.5 | `trust on` and `trust off` write the setting, and each says in plain words what Tardigrade will and will not answer. With no word it says which it is. | `test_G5_5_trust_on_and_off_write_the_setting_and_say_what_it_means` |
| G5.6 | `record` (setup's first one too) takes a session's tab and workspace labels from, in order: Herdr now, the record before, and the `tsv_path` table when that table has the session's id (a table another tool wrote, the case of a machine switching over). A Herdr call that times out on a loaded machine loses that one pane's labels, not all of them, and setup gives Herdr longer to answer. | `test_G5_6_the_record_keeps_labels_from_herdr_then_the_record_before_then_the_table` |
| G5.8 | The boot run logs how long after boot it began and what it waited for: the user's services coming up (Linux) and its own fixed wait, now the setting `boot_wait_s` (default 45 s, written into the boot service by setup; a shorter default only after a real machine has shown it safe). Census's line for a running restore and `report` show it. | `test_G5_8_the_boot_run_says_when_it_began_and_what_it_waited_for` |
| G5.9 | `setup`'s first record is a plain record: sessions that vanished together are held on the record as the timer's record holds them (G1.11), so installing or upgrading on a machine whose sessions are down forgets none of them. Only a person's own `record --force` lets them go (T35). | `test_G5_9_setup_keeps_sessions_that_are_down_on_the_record` |
| G5.10 | Due in 0.1.10 (T51). `setup` says which version was installed and which is now, and says it plainly when the new one is older; it never moves a machine backwards in silence. | Not built yet. |
| G5.11 | Due in 0.1.10 (T58). The lowest Python the tool runs on is named in the README and the skill, and CI runs it. Every command checks it first; below it, the command stops with the version found and where the tool does run, having made nothing. The script stays readable by Python 3.6's grammar so that line is reached. | Not built yet. |
| G5.4 | `uninstall` removes the service and the timer and touches no running session and no record. | Not verified by a test. |

### G7 Tests

The tests run on a person's own machine, so they must leave it as they found it.

| Codon | Claim | Proof |
|---|---|---|
| G7.1 | A test run that is stopped part-way (a timeout, a closed terminal, a setUp that fails) leaves no tmux server, no process and no folder behind. Each test registers its cleanup the moment its folder exists, every step of it runs even if one fails, and SIGTERM, SIGHUP and exit run the cleanups of the tests still running. | `test_G7_1_a_run_stopped_part_way_leaves_nothing_behind` |
| G7.2 | A test that checks a process has gone waits a bounded time for it to exit instead of reading once (the waiting screens of G4.4, a stopped run's processes in G7.1), and still fails if it stays (T42). | `test_G7_2_a_check_that_a_process_is_gone_waits_for_its_exit_and_still_catches_one_that_stays` |
| G7.3 | (0.1.9) A test named in the plan or in `PROMISES.json`, at any version, counts only if the runner really runs it: read from the syntax tree, never from text, as a method defined straight in a test class's body, once; never skipped whatever the machine (an honest skip on a condition of the machine, with its reason, is "not run here"); a body that does something; and, after a whole run through the file's own entry point (as CI runs it), reported PASSED. A self-test plants the bad cases: nested after a return, renamed, under an `if`, skipped, shadowed by a later empty one, only text in a string (T49). | `test_G7_3_a_test_named_for_a_claim_counts_only_if_the_runner_really_runs_it` |
| G7.4 | (0.1.9) Every place that states the version says the same one: the script's `VERSION`, the plugin manifest, the marketplace entry and the skill file if they carry one, and the changelog's top entry; the suite fails when one differs. The plugin manifest said 0.1.7 through 0.1.8, 0.1.8.1 and 0.1.8.2 (T50). | `test_G7_4_every_place_that_states_the_version_says_the_same_one` |

### Planned: what the hundred-session reboot asked for

Not built yet. Each row is a promise in `PROMISES.json`. When one is built, its codon moves into its gene's table
above with the name of its test, and the row's `test` field names the same test.

| Codon | Claim | Promise |
|---|---|---|
| G3.14 | `record` and `census` say beforehand which running sessions are on a login Tardigrade cannot give back: a login token in the environment with no account label, a label with no `account_command`, or a session started without a token on a machine whose start adds one. The "needs you" line says which change it saw (no token before and one now, a token before and none now, another token). With G3.12. | T27, due 0.1.10 |
| G3.15 | Before a reboot, `record` and `census` name (names only, never values) the variables a running session has that its own start would not give back. Tardigrade does not guess which matter and does not carry them by itself; `carry_env` stays the person's switch. With G3.12 and G3.14. | T30, due 0.1.10 |
| G4.6 | On a desktop, a window opens by itself after login for each returned session that had a window, once the restore is done. A setting that setup offers; off where there is no desktop. | T11, due 0.1.11 |
| G3.9 | `ready` says, before a planned reboot, which sessions are busy and which have background commands that will be cut. | T12, due 0.1.10 |
| G3.10 | `ready` names the sessions whose running background commands or scripts live under /tmp, because a reboot empties it. | T14, due 0.1.10 |
| G5.7 | `setup` lists the other things on the machine that would start Claude sessions at boot or login (units, agents, autostart entries, desktop icons), so the person can switch them off: one restorer per session. It changes none of them. | T16, due 0.1.11 |
| G3.11 | `ready` lists this user's services and timers that a reboot will change: enabled but stopped now (they will start), running now but not enabled (they will not come back), and timers whose only trigger counts from their own last run (after a reboot they never fire again by themselves). It lists; it does not judge or change them. | T19, due 0.1.10 |
| G3.13 | Opt-in, off by default: after a restore the counts alone (started, loaded, down; no names, no folders) are written where other users of the machine can read them, so one person can confirm a restore for another without entering their account. | T22, due 0.1.11 |
| G6.1 | `prep`: before a planned reboot each session is asked to write down what it must start again; the answers are kept with the record and given back to that session in its wake-up note. Asking a running session is a new kind of typing, so this needs its own invariant before it is built. | T23, due 0.2.0 |

## Promises

`PROMISES.json` holds what real machines, and the people using them, showed must change: what was seen, the
promise, its codon, and the version it is due in. Four tests keep it honest. A version cannot be released while a
promise due in it has no test. The version after that cannot be released until the row says on which real machine
the promise was seen to work. A date moves only with a written reason, and a promise that came from what the
person saw on his own machine moves only by his word. Rows are never deleted. A lesson that was heard and not
taken is written under `declined` with the reason.

## Invariants

These hold across every gene. A change that breaks one is a plan change first.

| Invariant | Proof |
|---|---|
| INV1 It never ends a Claude session and never ends a tmux session, with two exceptions, both in a tmux session it made itself in the same run: the leftover shell when the first start failed, which it replaces; and its own waiting screen (G4.4), which a session's start replaces, or which it ends, from one function and only after checking the session's mark, when that session turned out to be running elsewhere. | `test_INV1_it_never_ends_a_session` |
| INV2 It types two things only, each from one function, and only into a session it started itself in this run. One: its own wake-up note, once the session is loaded and idle with no question on its screen (G2.2, G2.20); `"wake": "idle"` switches it off. Two, which only the person can switch on: the trust answer (G2.12, G2.13). It answers no other dialog. Into a Herdr pane it types only the `tmux attach` of G4.2. | `test_INV2_it_types_two_things_only_each_from_one_function` |
| INV3 It never stores a secret. A login token leaves only a variable name and an 8-digit fingerprint. A variable whose name looks like a secret's is never recorded, carried or copied into a start file. | G1.6, G2.17, G2.18 |
| INV4 Never a second copy of a session. | G2.4, G2.9 |
| INV5 It reads only this user's processes. | G3.3 |
| INV6 Python standard library only, and no network. | `test_INV6_standard_library_only_and_no_network` |

The link between this plan and the tests is itself tested: every proof named here is a real test, and every codon
test has a codon here.

## Proven by hand on real machines, not by a test

On 0.1.7 (plan 1.7), 2026-11-21:

- **A shared Linux machine with 98 sessions, one real reboot**, its first on this skill (the older restore tool was
  switched off in the same minute this one was switched on). All 98 came back by themselves: 98 started, none
  failed, none retried. The boot run began 4 min 35 s after boot, started the machine's tmux server through the
  machine's own command (G2.26), brought the first session up 8 seconds later and the last 18 minutes after that;
  all 98 were loaded and counted 25 min 37 s after boot (G2.24). Modes word for word (90 of one spelling, 7 of
  another, 1 auto) (G2.14). 90 Herdr tabs attached and checked; six sessions had no tab because their workspaces
  were gone, and one because a person had started a copy in it. No trust question was asked. What it did badly
  is in the field notes and in `PROMISES.json`.

- An Ubuntu 24.04 laptop, Claude Code 2.1.330, **seven real reboots**, seven sessions (one of a team's working
  sessions with its Herdr tab; permission checks off; accept-edits; the home folder; one from a plain desktop
  window; two that nobody had written in). Reboot 1: five of seven up, and the five were not looked at for 25
  minutes (G2.29, G2.30 come from it). Reboot 2 landed in the middle of that run and brought back nothing: the lock
  (G2.31); after the fix the boot run was started again by hand, took the lock over and brought back seven of
  seven. Then five reboots, one a sudden reset: seven of seven running within four minutes of boot and loaded;
  the trust question answered for the two home-folder sessions, once within 4 seconds of the start (G2.12, G2.25,
  G2.32); the Herdr tab attached again and checked (G4.2); every command line clean (G2.19); accept-edits kept its
  mode (G2.14); the plain-window session came back on the named socket under its prefix (G1.9); the table written
  (G1.10). One of them with an env file and a tmux config: the variables arrived, each session started in its own
  folder, a session bus that was gone was replaced, the config was loaded (G2.17, G2.26, G2.27, G2.33). One with
  the note on and the account at its usage limit: nothing typed into five sessions that came back to a question
  (G2.20).
- An Intel Mac, macOS 13, auto-login on, FileVault off, **three real reboots** (one ungraceful), four sessions, two
  of them a team's working sessions in the home folder with Herdr tabs: four of four loaded between three and
  four minutes after boot each time, each login checked (G2.8), trust answered for both. Reboots 1 and 2 logged the tabs as
  attached when they were not: the tab's shell is zsh, which reads a bare `=name` as a command path. After the fix
  both tabs were attached, and the log line now comes from a check (G4.2).
- Linux, Claude Code 2.1.330, one real session ended and brought back through its start file with the env file of
  a real desktop: display and session bus present, no child marker, its own folder; the note was typed once it was
  idle, and the session answered it (G2.2, G2.19).
- A shared Linux machine with 98 sessions, read only: `revive --dry-run --all` against the restore tool in use
  there: 98 in both, none in only one, no difference in tmux name, folder, model, effort or recorded mode (G2.21).

On earlier versions:

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
- **G2.15 and G2.16** on a real reboot: stand-ins and a read-only dry run only. (The `server_command` of G2.26 and
  the `env_file` of G2.17 went through the 98-session reboot.)
- **A second user on the same machine**, installed with the quiet settings: his sessions were started (counted
  from outside, by program name); whether they came back as he left them is his to say.
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
