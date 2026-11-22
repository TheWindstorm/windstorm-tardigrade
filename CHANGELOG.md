# Changelog

## Unreleased

## 0.1.8 (not yet released)
Built from DNA Strand Master Plan 1.8, from the lessons of the first real reboot with 98 sessions (`PROMISES.json`
T1 to T21, T24) and four found on real machines while getting ready for the next ones (T25, T26, and two inside
T13). Every row due in 0.1.8 is built and named in the plan next to its test.

The first minutes after a reboot:

- **A start that is a little late is looked after at once** (G2.44), not at the next retry round 60 s later. On a
  Mac, the first start after login waits on the network for its login and missed the first look.
- **Several sessions start at a time** (G2.34): at most `starts_at_once` (default 4) started and not yet seen up.
  Each start is still decided on its own, right before it, with a fresh process list.
- **A waiting screen holds each place from the first minute** (G4.4). Each session to be started gets its tmux
  session at once, holding a small program (not a shell) that says the session is coming back, how many are back,
  and not to start it by hand. It takes no input. Its Claude replaces it in the same pane. A restore that stopped
  says so on the screen, and `census` counts what it left as "waiting, stopped" (G4.9).
- **Herdr tabs are attached at the start, and tried again in every pass** (G4.3). A workspace that is gone is
  made again under its old label, never focused (G4.5), and the tab it comes with is used, not left blank (G4.7).
  `revive --only` says what it did about the tab (G4.8).
- **The boot run says when it began and why** (G5.8): how long after boot the user's services came up, then its
  own wait, now the setting `boot_wait_s` (default 45 s). **`report`** prints the last restore's timeline (G3.8).
- **While a restore runs, census says so** (G3.6), with a desktop note and a Herdr notification at its start and
  end. With **`on_done`** set, a command of yours is run with the counts when it ends (G2.38).

What comes back:

- **Every start flag** (G2.39): `--plugin-dir`, `--settings`, `--name` and the rest, word for word. 0.1.7 carried
  only the permission mode, model, effort and added folders. A flag that cannot be carried is named in the log;
  README lists them. On macOS the argument list is now read from the kernel, so an argument with spaces stays one.
  **Visible change:** a session started with `--remote-control` now comes back with Remote Control on; 0.1.7
  brought it back without. The log says so the first time a record sees it. `--settings`, `--mcp-config` and
  `--agents` are carried as file paths only, never as inline JSON, and a value with the shape of a key or token
  is never carried.
- **The record, the table, the progress file and the log are yours only** (G2.42): files 0600, Tardigrade's
  folders 0700. They hold start flags word for word, and a Mac's home is open to other users by default.
- **A session its owner ended with `/exit` stays ended** (G2.36), and is not counted in a crash.
- **A session with no conversation is started new even when it is slow to say so** (G2.43). On a Mac whose
  start fetches a login first, 0.1.7 counted such a session up, then lost it.
- **A pinned session keeps its account label** (G2.41): the start file sets it, so a login wrapper need not.
- **Tab and workspace labels** from Herdr, the record before, or the `tsv_path` table (G5.6).

The wake-up note:

- **Per session, in its own words, spaced** (G2.37): `wake_names`, `wake_skip`, `wake_text`, and at most
  `notes_per_min` a minute (default 6). It says when /tmp was emptied. It is typed only into an empty input box; a
  dim placeholder in the box counts as empty (found on a real machine before any reboot).
- **Never beside a usage or limit notice** (G2.40), whatever its form. 0.1.7 missed Claude Code's "Usage limit
  reached ... /usage-credits" notice under an empty box and would have typed its note there (found on two real
  machines before any reboot).
- **Never into a conversation that ended on a usage limit, and a note held for the reset is named** (G2.45).
  On a real reboot, 0.1.7's note went into two sessions at their weekly limit; Claude Code held it, to run by
  itself at the reset about 9 hours later. A session at its limit that has not met it in its own conversation is
  not covered by the first part; the second part tells you its note is waiting ("note queued until the reset").

A name is not an identity:

- **`revive --only NAME` refuses a name two sessions carry** (G2.35) and lists them with their ids.
- **census lists names more than one session carries** (G3.7), and marks a session started by hand while its
  namesake was coming back.
- **census names each running session that will not come back, and why** (G3.12).
- **PROMISES.json**: twenty-four lessons from the first real reboot with 98 sessions, twelve of them from the agent
  that ran that machine, each with the version it is due in, and one lesson declined with its reason. Five tests
  (class `Promises`) refuse a version while a promise due in it is not built. No change to the script.
- README and plan: what that reboot proved, and what it did badly.

## 0.1.7 (2026-11-21)
Built from DNA Strand Master Plan 1.7. The fleet this skill came from is moving to it from its own restore kit;
this release is that kit's author's list of everything the kit does.

- **Nothing but the start command is on a command line** (G2.19). A command line is visible to every `pkill -f`.
  0.1.6 passed the wake-up message as an argument. Now each session's shell is started with "read this file", and
  the file, readable by you only, holds the settings and the start line.
- **The wake-up message is typed** (G2.2, G2.20), once Claude Code reports the session loaded and idle and its
  screen shows no question. A session that comes back to a question gets no message. A login is checked before
  anything is typed (G2.11).
- **A count after the starts** (G2.24): LOADED, UP-NOT-LOADED and DOWN, the last two by name. Loaded means Claude
  Code has written the session's own file, not that a process is up.
- **Sessions that vanish together stay on the record** (G1.11): more than half at once, and at least three, is a
  crash. They stay revivable for 12 hours.
- **A permission mode comes back word for word, never looser** (G2.14). This was already so; now a test says it.
- **For machines with their own ways**: `account_env` + `account_command` (a session pinned to an account starts
  through its wrapper, G2.15), `start_command` (G2.16), `env_file` (G2.17), `carry_env` (G2.18), `loose_socket`,
  `loose_prefix`, `loose_skip` (G1.9), `tsv_path` (G1.10), `load_max` (G2.23).
- **`revive --dry-run --all`** (G2.21): the exact start line of every recorded session, before a planned reboot.
- **Mac: who has to log in** (G5.3). Setup and the census now say that the boot agent runs only when you are
  logged in at the Mac's screen, and name the case of a Mac that logs in by itself as another user.
- **Herdr tab re-attach has a test** (G4.2), with a stand-in for the `herdr` command.
- `trust_wait_min` is now `watch_min`; the old name still works.

Found in review by the kit's author, before any machine ran this version:

- **The trust answer could have pressed keys into another dialog** (G2.25). 0.1.6 looked for the question anywhere
  on the screen. A resumed session redraws its old conversation, which can quote that dialog, and the keys would
  have gone to whatever dialog was really open. Now only the dialog at the foot of the screen counts, nothing is
  sent when a usage or limit dialog is near, and the screen is read again right before Enter. If you switched
  `trust on` under 0.1.6, update.
- **A machine's own tmux server** (G2.26): `tmux_conf`, or `server_command` for a server that has to be started
  a special way. The server now also gets the `env_file`'s variables.
- **An `env_file` no longer carries the starting shell's own variables** (folder, user id, terminal), and each
  start file ends by changing to the session's own folder (G2.27).
- **One process list per pass** (G2.28), for machines with thousands of processes.

Found by the first real reboot on this version (a Linux laptop, seven sessions):

- **Sessions that were up waited while another start was retried** (G2.29). Two sessions could not be started, and
  for the 25 minutes of retries nobody looked at the five that were up: no trust answer, no note, no Herdr tab.
  Now the sessions that are up are looked after in every pass.
- **A second restart during a revive no longer blocks the next one** (G2.31). The lock that keeps two revives
  apart outlived a reboot, and for two hours the next boot would have brought nothing back. The lock now says
  which boot and process took it. Found by reading the code before the second reboot, not by losing sessions.
- **A session nobody ever wrote in cannot be resumed** (G2.30). Claude Code keeps no conversation for it and says
  "No conversation found". Such a session now comes back as a new one with the same name, folder and flags.

## 0.1.6 (2026-11-21)
Built from DNA Strand Master Plan 1.6.

- **`trust on` / `trust off`** (G2.12, G2.13, G5.5): an opt-in. With it on, when a session Tardigrade brings back is
  asked "do you trust this folder?" for the folder it was already running in, Tardigrade answers Yes and logs it. It
  keeps looking for the question for up to 15 minutes after a boot, because a slow machine asks late. Off by default:
  without it Tardigrade still answers nothing.

## 0.1.5 (2026-11-21)
Built from DNA Strand Master Plan 1.5.

- **census marks a session parked at "trust this folder?"** as `trust?` and counts it as waiting (G3.5). Claude Code
  asks this on every start of a session in your home folder; such a session has not loaded and would otherwise look
  as if it were only slow.

## 0.1.4 (2026-11-21)
Built from DNA Strand Master Plan 1.4. Also found by the Mac.

- **A session that is still loading is running, not down** (G1.8). On a slow machine just after boot Claude Code
  can take minutes to load a session. 0.1.3 called it "down" for that time, and a record taken then would have
  dropped it from the list. The census now shows it as "starting" and the record keeps it.
- README and plan say what the Mac reboot proved.

## 0.1.3 (2026-11-21)
Built from DNA Strand Master Plan 1.3. Found by a real reboot of a Mac.

- **Machines where the `claude` command supplies the login token** (common on a Mac reached over ssh): 0.1.2
  refused to start any session that carried a token, so on such a machine it brought back nothing. Now sessions start
  through your own shell and command as before, and each one's login is checked afterwards against an 8-digit
  one-way fingerprint (G2.8).
- **Sessions pinned to different accounts** come back silent and are checked before anything can run on the wrong
  account (G2.11).
- **setup on a Mac** says whether the Mac logs in by itself after a restart.

## 0.1.2 (2026-11-21)
Built from DNA Strand Master Plan 1.2. The release that went through real reboots.

- **Proven on a real machine**: two reboots of an Ubuntu laptop, one a sudden reset with no shutdown. Every test
  session came back both times. Details in the README under Tested.
- **census** counts the sessions that are stopped at a question or a dialog: "Waiting for you: N" (G3.4).
- **open** detaches the windows it opens, so it returns at once, and gives each window the session's name.

## 0.1.1 (2026-11-21)
Built from DNA Strand Master Plan 1.1.

- **`skip_tmux_sockets`** (G1.7): name the tmux sockets another tool already restores, and Tardigrade leaves those
  sessions to it. Two restorers for one session is how a session gets started twice.
- **setup tells the truth**: after enabling, it asks systemd (or launchd) whether the boot service is on, and says
  "NOT set up" when it is not.
- The skill file says what to do when it was installed without the script.

## 0.1.0 (2026-11-21)
The pilot release. Built from DNA Strand Master Plan 1.0.

- **record**: every 5 minutes, writes down each live Claude Code session of yours: id, name, folder, model, effort,
  permission mode and where it lives. Never during a boot, never an empty list, never a token.
- **revive**: after a boot, starts each recorded session that is not running, as itself, inside tmux, 3 seconds
  apart, never a second copy, and tells it what happened. `--dry-run` and `--only NAME`.
- **census**: what is running, what runs with no window open, what is down. Other users' sessions are counted, not read.
- **open**: a terminal window for each hidden session.
- **setup / uninstall**: a boot service and a record timer for your own user (systemd or launchd). Setup says who
  has to log in after a restart.

Proven by 25 tests on Linux and one real session. Not yet proven through a real reboot, and not yet run on macOS.
