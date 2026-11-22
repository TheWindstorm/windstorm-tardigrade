# Windstorm Tardigrade

**Your Claude Code sessions come back after a reboot, as themselves. And one command tells you what is running.**

A tardigrade is a tiny animal that dries out, stops, and carries on when the water returns. This plugin is
modeled on it. When your machine restarts, crashes or loses power, every Claude Code session you had running comes
back by itself: the same conversation, the same name, the same folder, the same model and permission mode. Each one
is told what happened, so it can pick up where it left off.

It also answers a question that gets harder the more sessions you run: *what is still running?* Close a terminal
window and the session inside it can keep going for weeks. `census` lists every session, and marks the ones that run
with no window open.

> **Status: 0.1.7, a pilot.** It has been through ten real reboots on two machines (a Linux laptop and a Mac),
> two of them with no clean shutdown, and then a real reboot of a machine with 98 sessions. The first reboots on
> this version found faults, which is what they were for; after the fixes every session came back every time,
> all 98 included. That last reboot also showed what is still wrong: it took 25 minutes and told nobody what it
> was doing. See [Tested](#tested) for what is proven, and [PROMISES.json](PROMISES.json) for what it now owes.

## Install

You need Linux or macOS, Python 3, and [tmux](https://github.com/tmux/tmux/wiki) (`apt install tmux`,
`dnf install tmux` or `brew install tmux`).

```bash
claude plugin marketplace add TheWindstorm/windstorm-tardigrade
claude plugin install windstorm-tardigrade@windstorm-tardigrade
```

Then start Claude Code and say: **set up tardigrade**.

Without the plugin system, it is one file:

```bash
git clone https://github.com/TheWindstorm/windstorm-tardigrade
python3 -I windstorm-tardigrade/scripts/tardigrade.py setup
```

Setup copies that one file to `~/.local/share/windstorm-tardigrade/`, installs a boot service and a 5-minute
timer for your user (systemd on Linux, launchd on macOS), and writes down the sessions that are running now. It needs
no administrator rights and it tells you who has to log in after a restart (see below). Run setup again after you
update the plugin.

What a stock machine needs: nothing more, as long as `claude` is in `~/.local/bin` (where Claude Code's own installer
puts it), `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin` or `/bin`. Sessions are started with that `PATH`, not
with the boot service's, which on Linux usually lacks `~/.local/bin`. If yours is elsewhere, set `path` (see
[Settings](#settings)). 0.1.8 and 0.1.8.1 did not bring sessions back on a Linux machine whose service `PATH` lacks
the folder Claude Code is installed in; fixed in 0.1.8.2.

## What you get

| You say or type | What happens |
|---|---|
| `tardigrade setup` | Switches it on. From then on, sessions come back after a restart by themselves. |
| `tardigrade census` | Lists every session: running with a window, running with **no window open**, and down. |
| `tardigrade report` | The last restore's timeline (boot, first and last start, the count, the tabs) and what still needs you. |
| `tardigrade open` | Opens a terminal window for each hidden session. |
| `tardigrade revive` | Brings back, now, every recorded session that is not running. `--dry-run` shows the list first. |
| `tardigrade revive --only "Name"` | Brings back just that one. |
| `tardigrade revive --dry-run --all` | Before a planned reboot: shows the exact line that would start each recorded session, and starts nothing. |
| `tardigrade trust on` | Opt-in: after a restart, answer Claude Code's "trust this folder?" for a session's own folder. `trust off` undoes it. Off unless you switch it on. |
| `tardigrade uninstall` | Switches it off: removes the boot service, the record timer and the installed copy. Running sessions are not touched. Your settings, your record and the log are yours and stay; to remove them as well, delete `~/.config/windstorm-tardigrade` and `~/.local/state/windstorm-tardigrade` afterwards. |

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
a screen after a resume (which shows history), holds that session's note only, since the limit may long have reset, so they do not all wait and run at once at the reset. To drop a held note, press Esc in that
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
- On a shared machine it counts other users' Claude processes and reads nothing of theirs.
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
| `tmux_tmpdir` | | Set this if your shell sets `TMUX_TMPDIR`. |
| `herdr_tabs` | `true` | With Herdr, re-attach each session's tab after a revive. |
| `answer_trust` | `false` | Set by `trust on` / `trust off`. Answer "trust this folder?" for a revived session's own folder. |
| `watch_min` | `15` | After a boot, keep looking at the started sessions this long: to type the message once each is idle, to answer the trust question if you switched that on, and to count which have loaded. A slow machine loads late. |
| `load_max` | `0` | With a number here, each start waits (up to `load_wait_s`, default 120) while the machine's load is above it. |
| `skip_tmux_sockets` | `[]` | tmux socket names to leave alone, for when another tool already brings those sessions back. One restorer per session. |

On a small machine, set `starts_at_once` to 2, and, if the machine must stay usable while sessions come back, a
`load_max` of about twice its number of cores. Right after a boot most of the load is the boot itself.

The record is `~/.config/windstorm-tardigrade/lanes.json`. The log is
`~/.local/state/windstorm-tardigrade/tardigrade.log`.

### Fitting it to a machine that has its own ways

Most people need none of these. They are for machines where sessions are started through a wrapper, pinned to
accounts, or shown in another tool.

| Key | Default | Meaning |
|---|---|---|
| `path` | a standard one plus `~/.local/bin` | The `PATH` a revived session starts with: `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`. Set it if `claude` (or your start command) is elsewhere. |
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
| `on_done` | | A command run once when a restore ends. `{started}` `{loaded}` `{not_loaded}` `{down}` `{failed}` `{needs_you}` are put in (a session that came back on another login is `needs_you`, not `failed`), and the same counts are in `TARDIGRADE_STARTED` and so on. For a machine where someone or something should be told. |

Before a planned reboot, run `tardigrade revive --dry-run --all` and read the lines. That is exactly what will run.

## Tested

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
- 65 tests on Linux with stand-ins for `claude` and `herdr`, run on every push with Python 3.10 and 3.13.

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

The record-and-revive engine was built and run by an AI agent on The Windstorm's own fleet from November 2026, on a
machine with about 100 Claude Code sessions. This plugin is that engine made general, with the census added.

Other tools restore terminal layouts (tmux-resurrect, tmux-continuum, Zellij) and a few small ones resume Claude
Code sessions. Use what fits you. This one is for people who run many sessions and want them back without doing
anything.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
