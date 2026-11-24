# Windstorm Tardigrade

**Your Claude Code sessions come back after a reboot, as themselves. And one command tells you what is running.**

A tardigrade is a tiny animal that dries out, stops, and carries on when the water returns. This plugin is
modeled on it. When your machine restarts, crashes or loses power, every Claude Code session you had running comes
back by itself: the same conversation, the same name, the same folder, the same model and permission mode. Each one
is told what happened, so it can pick up where it left off.

It also answers a question that gets harder the more sessions you run: *what is still running?* Close a terminal
window and the session inside it can keep going for weeks. `census` lists every session, and marks the ones that run
with no window open.

> **Status: 0.1.10.1, a pilot.** On 0.1.7 it went through ten real reboots on two machines (a Linux laptop and a Mac),
> two of them with no clean shutdown, and then a real reboot of a machine with 98 sessions. The first reboots on
> that version found faults, which is what they were for; after the fixes every session came back every time, all
> 98 included. That last reboot also showed what was wrong: it took 25 minutes and told nobody what it was doing.
> See [Tested](#tested) for what is proven on which version, and [PROMISES.json](PROMISES.json) for what it owes.

## Install

You need Linux or macOS, Python 3, and [tmux](https://github.com/tmux/tmux/wiki) (`apt install tmux`,
`dnf install tmux` or `brew install tmux`).

```bash
claude plugin marketplace add TheWindstorm/windstorm-tardigrade
claude plugin install windstorm-tardigrade@windstorm-tardigrade
```

Then start Claude Code and say: **set up tardigrade**.

Installed as a plugin before? Run `claude plugin marketplace update windstorm-tardigrade`, then `claude plugin update
windstorm-tardigrade@windstorm-tardigrade` (seen to bring the new script once the plugin's version label had
changed, as it has in 0.1.9), then set up again: setup is what refreshes the copy the boot service and the timer run.

Without the plugin system, it is one file:

```bash
git clone --depth 1 --branch v0.1.10.1 https://github.com/TheWindstorm/windstorm-tardigrade
python3 -I windstorm-tardigrade/scripts/tardigrade.py setup
```

Setup copies that one file to `~/.local/share/windstorm-tardigrade/`, installs a boot service and a 5-minute timer for
your user (systemd on Linux, launchd on macOS), and writes down the sessions that are running now. It needs no
administrator rights and it tells you who has to log in after a restart (see below). Setup is what refreshes that
copy: run it again after you update the plugin. Setup leaves no group or other write permission on anything it makes,
whatever umask it was started with (it reads and sets the mode bits; it does not read access lists). If you ran setup
with a wide-open umask (umask 000, as some job runners start every job), run setup again with 0.1.10 or later: it
closes its own copy, units and install folder, and it names each folder above them, or above its settings and state
folders, whose mode bits and group members let another account in to write, with the one command that closes it
(`chmod go-w <folder>`); census repeats it until it is closed. A folder another account owns is named with its owner,
and no command: only an administrator can change it.

Taking the plugin off with Claude Code's own `claude plugin uninstall` does not take off what setup installed. The
boot service still brings your sessions back after a restart, and the 5-minute record timer still runs, both from
the copy in `~/.local/share/windstorm-tardigrade/`. To take them off, run
`python3 -I ~/.local/share/windstorm-tardigrade/tardigrade.py uninstall`. Your settings, record and log stay.
When the copy was set up from the plugin's folder and Claude Code's own list of plugins no longer has the plugin,
census, `report` and the log at each boot say so in one line, with that command; sessions are brought back as before.
If Claude Code's list cannot be read, that is said instead, and nothing changes.

Setup never moves a computer back by itself. Run from a script older than the installed copy (an old clone, a plugin
that was not updated), it stops, names both versions and has changed nothing; `setup --downgrade` is the word that
moves it back, and it is yours to give: an agent does not give it for you unasked. From the same or a newer script,
setup says which version was installed before and which is now. Census says when the Claude Code plugin's version
differs from the copy that runs at boot, with both versions, or that it could not be read.

What setup puts where: the copy, and its own list `folders-made.json`, in `~/.local/share/windstorm-tardigrade/`. On
Linux three unit files in `~/.config/systemd/user/`, and `systemctl --user enable` makes two links for them in that
folder, `default.target.wants/windstorm-tardigrade-revive.service` and
`timers.target.wants/windstorm-tardigrade-record.timer`. On a Mac two plists in `~/Library/LaunchAgents/`. Your
settings and the record are in `~/.config/windstorm-tardigrade/`, the log and Tardigrade's notes of past boots in
`~/.local/state/windstorm-tardigrade/`. A folder on that way that was not there is made by setup and written on its
list. `setup --dry-run` names the links and those folders before anything is written, and census names them after.
`uninstall` takes away the units, the links, the copy and the list, then each folder on the list that is empty now,
deepest first. It keeps your settings, record and log, says what a setup after it does with each, and says that
`auto_revive` now reads false in your settings and why that line stays. A copy set up by a version before 0.1.11 has
no list: uninstall then removes no folder above its own, and says so. A census, a report, a dry run and a setup that
stops before its first write make no file and no folder; a dry run's lines go to the screen only, not to the log.

What a fresh Linux machine needs (seen on a fresh Ubuntu 24.04): nothing more, as long as `claude` is in `~/.local/bin` (where Claude Code's own installer
puts it), `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin` or `/bin`. Sessions are started with those folders
first, then the other folders of the `PATH` of whoever runs the restore: at boot, the boot service's (which on
Linux usually lacks `~/.local/bin`); in a `revive` you type, your shell's. If yours is elsewhere, set `path` (see
[Settings](#settings)). 0.1.8 and 0.1.8.1 did not bring sessions back on a Linux machine whose service `PATH` lacks
the folder Claude Code is installed in; fixed in 0.1.8.2. On a Mac it has been seen working only on Macs that already
had Apple's developer tools and a newer Python installed; a Mac with neither has not been seen. 0.1.9 was seen
working with Claude Code 2.1.330; that is what was seen, not a lowest version.

## What you get

| You say or type | What happens |
|---|---|
| `tardigrade setup` | Switches it on. From then on, sessions come back after a restart by themselves. |
| `tardigrade census` | Lists every session: running with a window, running with **no window open**, and down. |
| `tardigrade report` | The last restore's timeline (boot, first and last start, the count, the tabs) and what still needs you. |
| `tardigrade open` | Opens a terminal window for each hidden session, with the terminal named in `TERMINAL` when that is set (it says so), else the first it finds. |
| `tardigrade revive` | Brings back, now, every recorded session that is not running. `--dry-run` shows the list first. |
| `tardigrade revive --only "Name"` | Brings back just that one. |
| `tardigrade revive --dry-run --all` | Before a planned reboot: shows the exact line that would start each recorded session, and starts nothing. |
| `tardigrade trust on` | Opt-in: after a restart, answer Claude Code's "trust this folder?" for a session's own folder. `trust off` undoes it. Off unless you switch it on. |
| `tardigrade uninstall` | Switches it off: removes the boot service, the record timer and the installed copy. Running sessions are not touched. Your settings, your record and the log stay, and it says where they are. A file in one of Tardigrade's folders that Tardigrade did not write stays as it is, and it is named, with why its folder stayed. What stayed is yours, to keep or delete after you look inside. It reads back what went: something of its own that could not be removed, or a timer that still runs, is named with the one command to use, and then it does not say off (exit 1; exit 3 when systemd or launchd gave no answer). While a restore is starting sessions it waits for it. When all is off, the last line says Tardigrade is off this computer; run again, it says it is already off. |

In Claude Code you can say these in plain words. In a terminal, each is
`python3 -I ~/.local/share/windstorm-tardigrade/tardigrade.py <command>`.

### The census

An example, with made-up names:

```
Windstorm Tardigrade census, 2026-11-21 15:02 EST (machine up 35 days)
Running: 5   with a window: 3   hidden (no window open): 2   down: 1
Comes back after a reboot: yes, by itself   Last record: 3 min ago, 5 session(s)

HIDDEN: running, but no window shows them
  Docs Rewrite                       idle     quiet 9 days   tmux default:docs              /home/you/docs
  Invoice Bot                        busy     quiet 40 s     tmux default:invoices          /home/you/invoices

WITH A WINDOW
  Website                            idle     quiet 12 min   tmux default:website           /home/you/site
  ...

DOWN: on the record, not running now
  Data Import                        last seen 2 h ago   /home/you/import
```

"Comes back after a reboot" is read at that moment from the things themselves: the installed copy, and the boot service enabled and the record timer active (on a Mac, both launchd jobs loaded). When one is not there it says NO and names it; when systemctl or launchctl gives no answer it says "could not be read", never yes, and names the step: log out and in (on a Mac, log in at its screen as this user), then run setup again. Up to 0.1.10 it was read from the setting alone, and could say yes with the boot service off.

"Quiet" is the time since the session last did anything. A hidden session that has been quiet for nine days is
usually one you forgot.

A session that is running but will not come back after a reboot is listed under RUNNING, BUT WILL NOT COME BACK,
with the reason: it was started after the last record (the next record takes it), a setting leaves it out, or it is
some program's own session.

## What comes back, and what does not

**Comes back:** the conversation, the session's name, its folder, its model, effort and permission mode, every
other start flag it had (see below), and its tmux session under the same name.

**Does not come back:** anything the session had running in the background: timers, watchers, background commands.
Tardigrade cannot restart those. Instead it tells each revived session, in one message, that they are gone, so the
session checks its notes and starts again what is still needed. The message is typed into the session once Claude
Code reports it loaded and idle, and never into one that shows a question. It costs one turn per session. To bring
sessions back silent instead, put `"wake": "idle"` in `~/.config/windstorm-tardigrade/config.json`.

**A note at a usage limit waits.** A message typed into a session whose login is at its limit is not lost: Claude
Code holds it and runs it by itself when the limit resets, perhaps hours later, with nobody there. So no note is typed
into a session whose conversation last ended on a usage or limit reply, nor beside a limit notice on its screen. A
session at its limit that has not yet met it in its own conversation can still get one; Tardigrade looks once more
after each note, and if it is held for the reset, the log and the counts say so ("note queued until the reset"). A
limit belongs to the login, not to one conversation: once a note just typed meets the limit (the limit words appear
after it), no other session on the same login gets a note in that run. A limit reply already in a conversation, or on
a screen after a resume (which shows history), holds that session's note only, since the limit may long have reset, so they do not all wait and run at once at the reset. And it holds it only while its reset is still ahead: when the reply says when it resets ("resets 12pm (America/New_York)", "resets Nov 24, 6pm (…)"), that moment is worked out from the reply's own time and zone, and once it has passed (with five minutes' margin) the note is typed and the log says so. A reply that does not say when, or names no zone, still holds it. To drop a held note, press Esc in that
session. Tardigrade does not press it for you. Your own words for the note (`wake_text`) must not read like a limit
notice ("hit your weekly limit", "continue automatically", "/usage-credits"): `setup` names such words, and the
default note is used in their place. The same goes for your status line: Tardigrade reads the lines under Claude
Code's input box however many there are, so a status line that prints words such as "usage limit" or "limit
reached" holds that session's note, and one that changes into such words right after a note marks its login as at
its limit. Keep those words out of your status line.

**A session you ended with `/exit` stays ended**, also when the machine restarts minutes later. Tardigrade
reads it from Claude Code's prompt history (`~/.claude/history.jsonl`), for the sessions on its record only: of
those lines it keeps the time and whether it is exactly `/exit` or `/quit`, and nothing else. To bring such a
session back anyway: `revive --only <its id>`. One known edge: a session being moved to another login at the very
moment of a restart (ended with `/exit` and resumed on the other login seconds later) may stay down; `revive --only
<its name>` brings it back.

**A session started outside your usual way** (say, from a script that skips your shell's setup, so a login
wrapper did not run) comes back your usual way, through your shell. If that gives it another login, it is listed as
"needs you" and nothing is typed into it.

**A variable you set by hand** (an `export` in the shell before you started the session) is not given back: a
revived session gets what your shell's usual setup gives it, the `env_file` and the names in `carry_env`, nothing
else. Name the ones a session needs in `carry_env` or `env_file`.

**A session nobody ever wrote in** has no conversation, so Claude Code cannot resume it. It comes back as a new
session with the same name, folder and flags.

**After a crash:** if more than half of your sessions vanish at the same moment (a tmux server that died, a
terminal program closed with everything in it), Tardigrade does not take that for you ending them. They stay on
the record for 12 hours, the census says so, and `tardigrade revive` brings them back.

**The trust question:** Claude Code asks "do you trust this folder?" on every start of a session whose folder is
your home folder. By default Tardigrade does not answer for you: the census marks such a session `trust?` until you
open its window and answer. If you would rather have it answered, say `tardigrade trust on`. Then, for a session
Tardigrade brings back, in the folder that session was already running in, it answers Yes. It answers no other
question, and it writes each answer in its log. It counts only the dialog that is open at the foot of the screen:
a conversation that merely quotes the question gets no key, and neither does a screen with a usage or limit
dialog on it.

### Start flags

A session comes back with the flags it was started with, word for word: `--plugin-dir`, `--settings`,
`--mcp-config`, `--allowed-tools`, `--name` (with the name the session has now) and the rest. These are not carried:

- the ones that name a conversation or pass a first message (`--resume`, `--continue`, `--session-id`,
  `--fork-session`, `--from-pr`, `--teleport`, `--print`, and the words of a first message). A revive gives its own
  `--resume`;
- the ones that would start something other than this session in this place (`--worktree`, `--tmux`, `--bg`,
  `--desktop`, `--cloud`, `--environment`);
- a flag whose value looks like it holds a secret: one that names a key, a token or an authorization header, or
  has the shape of a common key or token. It stays out of the record and the log;
- `--settings`, `--mcp-config` and `--agents` given as inline JSON: only a file's path is carried;
- a flag this version does not know, when the word after it may be its value.

Each flag that is not carried is named in the log the first time a record sees it, with the reason, never dropped
silently. A first message is never written down.

`--remote-control` is carried. A session started with it comes back with Remote Control on, which 0.1.7 did not
do, and the log says so the first time a record sees it.

Known edge until 0.1.12 (T70): the permission mode that census and the dry run show is the start flag. A session
that Claude Code runs in another mode was shown as "default" (seen once, with auto mode), and it came back in the
mode Claude Code kept for it.

The record, the table at `tsv_path`, the progress file and the log are readable by you only (0600), in folders
that are yours only (0700), because they hold start flags word for word.

**The first minutes:** a restore starts several sessions at a time (`starts_at_once`), so on a machine with many
sessions the last can take a while. From the first minute, each place a session will come back to (its tmux
session, and its Herdr tab) holds a waiting screen that says it is coming back, how many are back, and not to
start it by hand, because two copies of one session would both act. The screen takes no input. The session
replaces it when its turn comes. `census` says that a restore is running, and `report` prints its timeline after.

**Where they come back:** inside tmux, so a session outlives its window. A session that was already in tmux returns
to the same tmux session name. A session that ran in a plain terminal window returns in Tardigrade's own tmux
(socket `tardigrade`) with no window, and `tardigrade open` shows it.

## Who has to log in after a restart

Setup tells you this for your machine. It never changes it for you.

- **Linux.** With *lingering* on for your user, sessions come back about a minute after boot with nobody logged in.
  With it off, they come back when you log in. To switch it on, a person runs once:
  `sudo loginctl enable-linger $USER`.
- **macOS.** Sessions come back when you are logged in at the Mac's own screen. A login over ssh does not count,
  and neither does another user's login, even if the Mac logs that user in by itself. With FileVault on, a
  restarted Mac waits at its unlock screen and nothing runs until someone types the password there. The census
  says which of these your Mac is.

## Safety

- **Never a second copy.** A session id that any live process of yours holds is never started. Two copies of one
  session would both act.
- **A read that failed is "cannot tell", never "nothing runs".** Each start is decided on a read of the process list
  taken right before it, after any `load_max` wait. If that read fails (`ps` gives an error, or no answer within
  30 s), a restore starts nothing in that pass, says so in its log, and reads again at the next. If the process list
  or a tmux server's list of panes cannot be read, census says it cannot tell and gives no count of running or down
  sessions, and the record is kept exactly as it was. Only the tmux servers the record depends on count (each
  recorded session's, tmux's default, `loose_socket`, `tsv_socket`). Census names a record that has not been written
  for an hour, and why. A restore that cannot read the process list for all of `retry_min` starts nothing, and
  `report` and `on_done` say so. Known edge until 0.1.11 (T54): if the session file Claude Code
  keeps for one running session (in its `sessions` folder) cannot be read, and that session was not started with
  `--resume` of a recorded id, the record counts it as ended and the next restart does not bring it back.
  Known edge until 0.1.12 (T56): with Claude Code installed through npm at 2.1.112 or older (`readlink -f $(command
  -v claude)` ends in `cli.js`), a session started by hand cannot be seen as running until it has loaded: Claude Code
  runs under node's name at first, then renames itself and drops its start arguments. A restore can start a second
  copy of such a session. Update Claude Code (2.1.113 and later ship the native program, through npm too), and start
  nothing by hand while a restore runs.
- **A session comes back with the permission mode it had.** If it ran with permission checks switched off, it comes
  back that way, with nobody watching, and the opening message gives it one turn. You chose that mode when you
  started it; Tardigrade does not widen it. If you do not want sessions to act by themselves after a restart, use
  `"wake": "idle"`.
- It never ends a session and never answers a dialog for you. If a revived session stops at a question, it waits
  there for you. The one exception is yours to switch on: `trust on` (above). It is two keys, sent only when the
  screen shows the cursor on "Yes, I trust".
- It types one thing of its own, the wake-up message, and only into a session it started itself that is loaded,
  idle and shows no question. `"wake": "idle"` switches that off.
- **Nothing but the start command is on a command line.** A command line is visible to every `pkill -f WORD` on the
  machine, so the message is typed, and a session's settings are in a start file only you can read. Claude Code's
  own command line is what you would have typed: its flags and `--resume`.
- **A permission mode comes back word for word.** It is never made looser.
- It never stores a token or a key. For a login token it keeps the variable's name and an 8-digit one-way
  fingerprint, and uses it to check that a session came back on the same login. The check comes before anything
  is typed: a session whose login changed is listed as "needs you", with no message typed and nothing run on the
  wrong account. A variable whose name looks like a secret's is never recorded or copied, whatever the settings say.
- On a shared machine it counts other users' Claude processes by program name; its own code reads nothing else of
  theirs. (On Linux the system's `ps`, which it runs for the process list, opens more of every process: see
  `skip_cwd_prefix`.)
- Python standard library only. No network. It prints names, folders and counts, never message text.
- The record is never overwritten by a machine that is still starting, and never by an empty list.

## Settings

`~/.config/windstorm-tardigrade/config.json`, all optional:

| Key | Default | Meaning |
|---|---|---|
| `wake` | `"note"` | `"note"` tells each revived session what happened. `"idle"` says nothing. |
| `wake_names` / `wake_skip` | `[]` | Patterns of session names: `wake_names` get the note even with `"wake": "idle"`; `wake_skip` never get it. |
| `wake_text` | | Your own words for the note. `{when}` is the time it came back. |
| `notes_per_min` | `6` | At most this many notes typed in one minute, so many sessions do not all take a turn at once. |
| `stagger_s` | `3` | Seconds between two session starts. |
| `retry_gap_s` | `60` | Seconds between retry rounds for a start that did not come up. A start that is only a little late is looked at in every pass meanwhile. |
| `starts_at_once` | `4` | At most this many sessions started and not yet seen up. The wait after each start overlaps with the next starts; the decision to start is still made one session at a time. Lower it on a small machine. |
| `boot_window_min` | `40` | The boot service acts only on a machine up for less than this. |
| `boot_wait_s` | `45` | Seconds the boot service waits before its run. Run setup again after changing it. |
| `retry_min` | `25` | How long failed starts are retried. The network can lag at boot. |
| `shell` | your login shell | `bash` or `zsh`. Sessions start through it, so your own `claude` setup loads. |
| `tmux_tmpdir` | | Where tmux keeps its sockets. Empty: `TMUX_TMPDIR` when the run has it (census says so), else `/tmp`. Set this if your shell sets `TMUX_TMPDIR`: the boot service does not have it. |
| `herdr_tabs` | `true` | With Herdr, re-attach each session's tab after a revive. Tardigrade attaches tabs to a Herdr that is running; it never starts Herdr itself. When Herdr is not running, the log says so at once and `census` says how many tabs wait for it. Have Herdr's server start at login by itself (a launch agent on a Mac, a user service on Linux, running `herdr server`), not only from a terminal window that the desktop may or may not reopen. |
| `answer_trust` | `false` | Set by `trust on` / `trust off`. Answer "trust this folder?" for a revived session's own folder. |
| `watch_min` | `15` | After a boot, keep looking at the started sessions this long: to type the message once each is idle, to answer the trust question if you switched that on, and to count which have loaded. A slow machine loads late. |
| `load_max` | `0` | With a number here, each start waits (up to `load_wait_s`, default 120) while the machine's load is above it. |
| `skip_tmux_sockets` | `[]` | tmux socket names to leave alone, for when another tool already brings those sessions back. One restorer per session. |
| `skip_cwd_prefix` | `[]` | Folders to leave alone, for when another tool runs its own agents with Claude Code there. A claude process working inside one is counted, and Tardigrade's own code reads only its folder (from the system): not its environment, its arguments, its status file or its conversation. It is never recorded or brought back. On Linux, the system's `ps`, which Tardigrade runs to list processes, opens more than that for every process on the machine (its environment and arguments too; seen with strace, procps-ng 4.0.4) and passes back only process id, parent, user, state and program name. From 0.1.11 the list is read from /proc directly (T47). |

On a small machine, set `starts_at_once` to 2, and, if the machine must stay usable while sessions come back, a
`load_max` of about twice its number of cores. Right after a boot most of the load is the boot itself.

The record is `~/.config/windstorm-tardigrade/lanes.json`. The log is
`~/.local/state/windstorm-tardigrade/tardigrade.log`.

### Fitting it to a machine that has its own ways

Most people need none of these. They are for machines where sessions are started through a wrapper, pinned to
accounts, or shown in another tool.

Check this before the first reboot on a machine. A session started with a login token in its environment, and no
label your start can read, comes back on whatever login the plain `claude` finds, which may be another account.
If every session on the machine should run on one login, put the wrapper that fetches it into `claude_command`.
`tardigrade revive --dry-run --all` shows each session's start line, so you can see this before you need it.
Known edge until 0.1.11 (T57): a session started on an account has been seen to lose its label from its
record row, so the next restart started it on the machine's own login without a word. After starting such a session,
check that its line in `tardigrade revive --dry-run --all` ends in `[account NAME]`.

| Key | Default | Meaning |
|---|---|---|
| `path` | a standard one plus `~/.local/bin` | The `PATH` a revived session starts with. Unset: `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`, then every other whole folder of the `PATH` of whoever runs the restore (at boot: the boot service; in a `revive` you type: your shell), once each. Set: exactly what you write. Set it if `claude` (or your start command) is elsewhere. |
| `claude_command` | `"claude"` | The command that starts Claude Code. The session's flags and `--resume <id>` are added to it. |
| `account_env` | | The name of an environment variable that holds a session's account label, if your wrapper sets one. A label, never a secret. |
| `account_command` | | Used in place of `claude_command` for a session that had a label, for example `"my-wrapper use {account}"`. The start file sets the `account_env` variable to the label before it runs this command, so the wrapper need not put it back. |
| `start_command` | | Your own whole start line, in place of the three above. Placeholders: `{session_id}` `{cwd}` `{name}` `{model}` `{effort}` `{mode}` `{account}` `{tmux_session}` and `{flags}` (the session's exact flags). Each is quoted for the shell. |
| `env_file` | | A file of `NAME=value` lines (or NUL-separated, as `env -0` writes) added to each revived session's environment. For a desktop's display and session bus, which a boot service does not have. Names that look like a secret's are left out, and so are the variables that belong to the starting shell (its folder, user id, terminal). |
| `tmux_conf` | | A tmux config file for a tmux server that Tardigrade has to start. |
| `server_command` | | Your own command that starts the tmux server, run once when none is up, for a machine that starts its server a special way. `{socket}` is the socket path. `server_socket` names the one socket it is for. |
| `carry_env` | `["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]` | Settings variables a session keeps: the value it had is recorded and set again. |
| `loose_socket` | `"tardigrade"` | The tmux socket name where sessions that were not in tmux come back. |
| `loose_prefix` | | Put before the tmux name of such a session. |
| `loose_skip` | `[]` | Patterns. A session outside tmux whose name matches is not recorded. `"^$"` means: no name. |
| `herdr_default_workspace` | | With Herdr: the workspace for a session that has a tab label and no workspace. |
| `tsv_path` | | Also write the record as a tab-separated table with eight columns at this path, for tools that read one. `tsv_socket` limits it to one tmux socket name. |
| `lost_keep_h` | `12` | How long sessions that vanished together stay on the record as sessions to bring back. |
| `on_done` | | A command run once when a restore ends. `{started}` `{loaded}` `{not_loaded}` `{down}` `{failed}` `{needs_you}` are put in (a session that came back on another login is `needs_you`, not `failed`), and the same counts are in `TARDIGRADE_STARTED` and so on. `{herdr_tabs_not_attached}` (and `TARDIGRADE_HERDR_TABS_NOT_ATTACHED`) is how many tabs were left because Herdr was not running. For a machine where someone or something should be told. |

Before a planned reboot, run `tardigrade revive --dry-run --all` and read the lines. That is exactly what will run.

## Tested

Seen on 0.1.9, 2026-11-23:

- **A real restart of a Mac** (an Intel desktop, macOS 13, in its main account, which carries Apple's developer
  tools and a newer Python; Claude Code 2.1.330), set up over 0.1.8.1, with two sessions that had Herdr tabs and
  Herdr not running. Both came back by themselves, one process each, were asked "trust this folder?" and answered
  (`trust on`), and were loaded and woken four minutes after boot. The first pass said at once that Herdr was not
  running and that 2 tabs would wait for it; 30 minutes later the log said its server never came up and left the
  tabs as they were. Nothing tried to start Herdr. The record ran during the watch, once every start was done.
- **A folder left alone, on that Mac:** a `claude` sat at its first screen in a `skip_cwd_prefix` folder. A
  whole-system file watch showed nothing of Tardigrade opening its session file or its conversation. A log of every
  `ps` and `lsof` it ran (steered: a shim on `PATH`) showed only the read of its folder naming that process. The
  in-process read of a process's arguments could not be watched there.
- **The uninstall line, as printed** (a fresh Ubuntu 24.04 VM, Claude Code 2.1.330, the plugin installed):
  `python3 -I ~/.local/share/windstorm-tardigrade/tardigrade.py uninstall` removed the boot service, the timer and
  the installed copy, and kept settings, record and log.

Proven on 0.1.7, on real machines, 2026-11-21:

- **A real reboot of a shared Linux machine with 98 sessions**, the first time that machine relied on this skill.
  All 98 came back by themselves: none failed, none needed a second try, each with its own permission mode, model
  and folder, through the machine's own command for starting its tmux server and with its desktop's variables.
  **It took too long and said nothing while it worked:** the starts ran one at a time for 18 minutes, the Herdr
  tabs were attached only at the end, and all 98 were loaded 25 and a half minutes after boot. Twenty minutes in,
  the machine's owner saw an empty tab, took his session for lost and started a second copy by hand. 90 of the
  tabs were attached; six sessions had no tab because their Herdr workspaces had not come back. Each of these is
  now a promise with a version (see below).

- **Seven real reboots of a Linux laptop** (Ubuntu 24.04, Claude Code 2.1.330) with seven sessions: one of a team's
  own working sessions with its Herdr tab, one with permission checks off, one that accepts edits, one in the home
  folder, one that had been in a plain desktop window, and two that nobody had ever written in. The first two
  reboots found three faults (see the changelog); they were fixed and the reboots repeated. On the five after
  that, one of them a sudden reset with no shutdown, all seven were running again within four minutes of boot and
  all seven loaded, with nothing typed by anyone. Each time: the two home-folder sessions were asked "trust this folder?" and
  answered (`trust on`), the Herdr tab was attached again, the two empty sessions came back new under their names,
  and every command line was Claude Code's flags and `--resume`, nothing else.
- **A restart in the middle of a revive** left a lock behind, and the next boot brought back nothing. That was
  the fault. After the fix, the boot run was started again by hand on that machine, took the lock over and brought
  all seven back. A second real reboot landing in the middle of a run of the fixed version is covered by a test,
  not yet by a machine.
- **Three real reboots of a Mac** (Intel, macOS 13, logs in by itself, FileVault off), one of them an ungraceful
  quick restart, with four sessions, two of them a team's working sessions in the home folder with Herdr tabs.
  All four were loaded between three and four minutes after boot each time, and each session's login was checked. The first two
  showed that the Herdr tabs were not really attached on a Mac (its shell reads `=name` its own way); after the
  fix both tabs were attached, and the log now checks that instead of assuming it.
- **The typed message, on a real session** (Linux, Claude Code 2.1.330): ended, brought back through its start
  file, the message typed once it was idle; the session showed its earlier conversation and answered.
- **The message withheld**: on a laptop whose account was at its usage limit, five sessions came back to Claude
  Code's "limit reached" question. Nothing was typed into any of them, and the log named all five.
- **An env file, a tmux config and a stale session bus** on a real boot: the variables reached the sessions, each
  session started in its own folder, the config was loaded, and a bus address that no longer existed was replaced
  by the user's own.
- **About 100 sessions, read only**: on a shared Linux machine with 98 sessions, `revive --dry-run --all` was
  compared line by line with the restore tool that machine uses today: the same 98 sessions, each with the same
  tmux name, folder, model, effort and recorded permission mode. Nothing was started.
- 119 tests on Linux with stand-ins for `claude` and `herdr`, run on every push with Python 3.10 and 3.13.

Proven on earlier versions only (0.1.1 to 0.1.6): opening windows for hidden sessions on a Linux desktop.

Not yet proven:

- **A session acting on the message after a real reboot.** It is proven after a real end and return of one
  session, not after a restart of the whole machine: the test machines' account was at its usage limit.
- **With nobody logged in at all.** The laptop logs into its desktop by itself, so lingering alone was not isolated.
- **A Mac with FileVault on, or one that logs in as another user**: setup and the census say what will happen; no
  such Mac was rebooted. Opening windows on a Mac.
- **A session pinned to an account by a wrapper** and **a start command of your own**: proven with stand-ins, and
  by a read-only dry run on a machine that uses them, not through a reboot.
- **Windows** is not supported. Under WSL, use it inside the Linux system.

Things the real machines taught us, which the tests now hold: Claude Code asks "do you trust this folder?" on every
start of a session in the home folder, even with permission checks off; a session on an account at its limit comes
back to a question; a session nobody ever wrote in cannot be resumed; a slow machine can take minutes to load a
session; and a line that works in one shell can fail in another.

The plan this is built from, with each claim and its proof, is
[DNA-STRAND-MASTER-PLAN.md](DNA-STRAND-MASTER-PLAN.md).

### What it owes

[PROMISES.json](PROMISES.json) lists what real use has shown must change, and the version each change is due in:
starting several sessions at a time, attaching each tab as its session comes up, saying on the screen that sessions
are on their way, waking sessions that came back idle, and twenty more. It is not a wish list. The tests refuse a version while a promise due in it is
not built, and refuse the next one until the promise has been seen to work on a real machine.

## Where it comes from

The record-and-revive engine was built by an AI agent in November 2026 and has run since then on the computers it was
built on, one of them a machine with about 100 Claude Code sessions. This plugin is that engine made general, with the census added.

Other tools restore terminal layouts (tmux-resurrect, tmux-continuum, Zellij) and a few small ones resume Claude
Code sessions. Use what fits you. This one is for people who run many sessions and want them back without doing
anything.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
