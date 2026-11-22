# Windstorm Tardigrade

**Your Claude Code sessions come back after a reboot, as themselves. And one command tells you what is running.**

A tardigrade is a tiny animal that dries out, stops, and carries on when the water returns. This plugin is
modeled on it. When your machine restarts, crashes or loses power, every Claude Code session you had running comes
back by itself: the same conversation, the same name, the same folder, the same model and permission mode. Each one
is told what happened, so it can pick up where it left off.

It also answers a question that gets harder the more sessions you run: *what is still running?* Close a terminal
window and the session inside it can keep going for weeks. `census` lists every session, and marks the ones that run
with no window open.

> **Status: 0.1.7, a pilot.** Versions up to 0.1.6 went through real reboots of a Linux laptop (one of them a
> sudden reset with no shutdown) and of a Mac, and brought every test session back. Its first Mac reboot brought
> back none, which is how two faults were found and fixed. 0.1.7 changes how a session is started, so those
> reboots are being done again. See [Tested](#tested) for exactly what is proven, on which version, and what is not.

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

## What you get

| You say or type | What happens |
|---|---|
| `tardigrade setup` | Switches it on. From then on, sessions come back after a restart by themselves. |
| `tardigrade census` | Lists every session: running with a window, running with **no window open**, and down. |
| `tardigrade open` | Opens a terminal window for each hidden session. |
| `tardigrade revive` | Brings back, now, every recorded session that is not running. `--dry-run` shows the list first. |
| `tardigrade revive --only "Name"` | Brings back just that one. |
| `tardigrade revive --dry-run --all` | Before a planned reboot: shows the exact line that would start each recorded session, and starts nothing. |
| `tardigrade trust on` | Opt-in: after a restart, answer Claude Code's "trust this folder?" for a session's own folder. `trust off` undoes it. Off unless you switch it on. |
| `tardigrade uninstall` | Switches it off. Running sessions and your record are not touched. |

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

## What comes back, and what does not

**Comes back:** the conversation, the session's name, its folder, its model, effort and permission mode, and its
tmux session under the same name.

**Does not come back:** anything the session had running in the background: timers, watchers, background commands.
Tardigrade cannot restart those. Instead it tells each revived session, in one message, that they are gone, so the
session checks its notes and starts again what is still needed. The message is typed into the session once Claude
Code reports it loaded and idle, and never into one that shows a question. It costs one turn per session. To bring
sessions back silent instead, put `"wake": "idle"` in `~/.config/windstorm-tardigrade/config.json`.

**After a crash:** if more than half of your sessions vanish at the same moment (a tmux server that died, a
terminal program closed with everything in it), Tardigrade does not take that for you ending them. They stay on
the record for 12 hours, the census says so, and `tardigrade revive` brings them back.

**The trust question:** Claude Code asks "do you trust this folder?" on every start of a session whose folder is
your home folder. By default Tardigrade does not answer for you: the census marks such a session `trust?` until you
open its window and answer. If you would rather have it answered, say `tardigrade trust on`. Then, for a session
Tardigrade brings back, in the folder that session was already running in, it answers Yes. It answers no other
question, and it writes each answer in its log.

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
| `stagger_s` | `3` | Seconds between two session starts. |
| `boot_window_min` | `40` | The boot service acts only on a machine up for less than this. |
| `retry_min` | `25` | How long failed starts are retried. The network can lag at boot. |
| `shell` | your login shell | `bash` or `zsh`. Sessions start through it, so your own `claude` setup loads. |
| `tmux_tmpdir` | | Set this if your shell sets `TMUX_TMPDIR`. |
| `herdr_tabs` | `true` | With Herdr, re-attach each session's tab after a revive. |
| `answer_trust` | `false` | Set by `trust on` / `trust off`. Answer "trust this folder?" for a revived session's own folder. |
| `watch_min` | `15` | After a boot, keep looking at the started sessions this long: to type the message once each is idle, to answer the trust question if you switched that on, and to count which have loaded. A slow machine loads late. |
| `load_max` | `0` | With a number here, each start waits (up to `load_wait_s`, default 120) while the machine's load is above it. |
| `skip_tmux_sockets` | `[]` | tmux socket names to leave alone, for when another tool already brings those sessions back. One restorer per session. |

The record is `~/.config/windstorm-tardigrade/lanes.json`. The log is
`~/.local/state/windstorm-tardigrade/tardigrade.log`.

### Fitting it to a machine that has its own ways

Most people need none of these. They are for machines where sessions are started through a wrapper, pinned to
accounts, or shown in another tool.

| Key | Default | Meaning |
|---|---|---|
| `claude_command` | `"claude"` | The command that starts Claude Code. The session's flags and `--resume <id>` are added to it. |
| `account_env` | | The name of an environment variable that holds a session's account label, if your wrapper sets one. A label, never a secret. |
| `account_command` | | Used in place of `claude_command` for a session that had a label, for example `"my-wrapper use {account}"`. |
| `start_command` | | Your own whole start line, in place of the three above. Placeholders: `{session_id}` `{cwd}` `{name}` `{model}` `{effort}` `{mode}` `{account}` `{tmux_session}` and `{flags}` (the session's exact flags). Each is quoted for the shell. |
| `env_file` | | A file of `NAME=value` lines (or NUL-separated, as `env -0` writes) added to each revived session's environment. For a desktop's display and session bus, which a boot service does not have. Names that look like a secret's are left out. |
| `carry_env` | `["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]` | Settings variables a session keeps: the value it had is recorded and set again. |
| `loose_socket` | `"tardigrade"` | The tmux socket name where sessions that were not in tmux come back. |
| `loose_prefix` | | Put before the tmux name of such a session. |
| `loose_skip` | `[]` | Patterns. A session outside tmux whose name matches is not recorded. `"^$"` means: no name. |
| `herdr_default_workspace` | | With Herdr: the workspace for a session that has a tab label and no workspace. |
| `tsv_path` | | Also write the record as a tab-separated table with eight columns at this path, for tools that read one. `tsv_socket` limits it to one tmux socket name. |
| `lost_keep_h` | `12` | How long sessions that vanished together stay on the record as sessions to bring back. |

Before a planned reboot, run `tardigrade revive --dry-run --all` and read the lines. That is exactly what will run.

## Tested

Proven (on the version named in each line; none of the reboots below was on 0.1.7 yet):

- **Two real reboots**, 2026-11-21, an Ubuntu 24.04 laptop, Claude Code 2.1.323: one clean restart and one sudden
  reset with no shutdown. Three sessions were running: one with permission checks on, one with them off, and one in
  a plain terminal window. Both times all three came back as the same sessions, with their names and folders, in
  under three minutes, with nothing typed by anyone. After the reset the record on disk was intact. A second tool
  restored a fourth session on the same machine in the same second, and neither touched the other's
  (`skip_tmux_sockets`).
- **A third reboot** of that laptop on the released version after a plugin update: three of three again.
- **One real reboot of a Mac** (Intel, macOS 13, logs in by itself, FileVault off), version 0.1.3: the launchd
  agent ran 166 seconds after boot and both test sessions came back as themselves. On that Mac the `claude` command
  supplies the login token, and each session's login was checked. The reboot before it, on 0.1.2, brought back
  none of them; 0.1.3 and 0.1.4 are the fixes.
- **`trust on` through a real reboot** of that laptop, version 0.1.6: four sessions, one of them in the home folder.
  All four came back. The one in the home folder was asked "do you trust this folder?", Tardigrade answered Yes
  five seconds later and logged it, and the session loaded. It answered nothing else: all four then stopped at the
  account's "limit reached" question and waited.
- **Opening windows** on that laptop's real desktop: three hidden sessions, three windows.
- **Acting on the opening message**: one real session on Linux, Claude Code 2.1.330. It was recorded, ended and
  brought back, and it read its notes and answered.
- The census on a shared Linux machine with 98 sessions: a quarter of a second, and its list matched a separate
  tool's list, 98 of 98.
- 50 tests on Linux with a stand-in for `claude`, run on every push with Python 3.10 and 3.13: record, revive, never
  a second copy, once per boot, a clean environment, the typed message, the start file, the census, and the rules
  under Safety.
- On a shared Linux machine with 98 sessions, 0.1.7's `revive --dry-run --all` was compared line by line with the
  restore tool that machine uses today: the same 98 sessions, each with the same tmux name, folder, model, effort
  and recorded permission mode. Nothing was started.

Not yet proven:

- **A session acting on the opening message after a real reboot.** On the test laptop the account had reached its
  usage limit, so each revived session stopped at Claude Code's own "limit reached" question and waited there. The
  census showed all of them as "waiting for you". That is the right behaviour, and it is not the same proof.
- **With nobody logged in at all.** The boot service started before any remote login, but that laptop also logs into
  its desktop by itself, so lingering alone was not isolated.
- **A Mac with FileVault on, or one that does not log in by itself**: setup says what will happen; no such Mac was rebooted. Opening windows on a Mac.
- **Herdr** tab re-attach on a real machine. The test uses a stand-in for the `herdr` command.
- **About 100 sessions through a real reboot.** The most brought back at once so far is four.
- Sessions pinned to different accounts by a token: proven with the stand-in only.
- **Windows** is not supported. Under WSL, use it inside the Linux system.

On a slow machine Claude Code can take a couple of minutes to load a session after boot. During that time the
census shows it as "starting".

Two more things the real machines taught us: Claude Code asks "do you trust this folder?" for a new folder even when
permission checks are off, and a session on an account at its limit comes back to a question. Tardigrade answers
neither. It leaves them for you and counts them.

The plan this is built from, with each claim and its proof, is
[DNA-STRAND-MASTER-PLAN.md](DNA-STRAND-MASTER-PLAN.md).

## Where it comes from

The record-and-revive engine was built and run by an AI agent on The Windstorm's own fleet from November 2026, on a
machine with about 100 Claude Code sessions. This plugin is that engine made general, with the census added.

Other tools restore terminal layouts (tmux-resurrect, tmux-continuum, Zellij) and a few small ones resume Claude
Code sessions. Use what fits you. This one is for people who run many sessions and want them back without doing
anything.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
