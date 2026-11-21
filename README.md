# Windstorm Tardigrade

**Your Claude Code sessions come back after a reboot, as themselves. And one command tells you what is running.**

A tardigrade is a tiny animal that dries out, stops, and carries on when the water returns. This plugin is
modeled on it. When your machine restarts, crashes or loses power, every Claude Code session you had running comes
back by itself: the same conversation, the same name, the same folder, the same model and permission mode. Each one
is told what happened, so it can pick up where it left off.

It also answers a question that gets harder the more sessions you run: *what is still running?* Close a terminal
window and the session inside it can keep going for weeks. `census` lists every session, and marks the ones that run
with no window open.

> **Status: 0.1.3, a pilot.** It has been through two real reboots of a Linux laptop, one of them a sudden reset
> with no shutdown, and brought every test session back both times. It has not yet run on a Mac. See
> [Tested](#tested) for exactly what is proven and what is not.

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
Tardigrade cannot restart those. Instead it tells each revived session, in one opening message, that they are gone,
so the session checks its notes and starts again what is still needed. That message costs one turn per session. To
bring sessions back silent instead, put `"wake": "idle"` in `~/.config/windstorm-tardigrade/config.json`.

**Where they come back:** inside tmux, so a session outlives its window. A session that was already in tmux returns
to the same tmux session name. A session that ran in a plain terminal window returns in Tardigrade's own tmux
(socket `tardigrade`) with no window, and `tardigrade open` shows it.

## Who has to log in after a restart

Setup tells you this for your machine. It never changes it for you.

- **Linux.** With *lingering* on for your user, sessions come back about a minute after boot with nobody logged in.
  With it off, they come back when you log in. To switch it on, a person runs once:
  `sudo loginctl enable-linger $USER`.
- **macOS.** Sessions come back when you log in. With FileVault on, a restarted Mac waits at its unlock screen and
  nothing runs until someone types the password there.

## Safety

- **Never a second copy.** A session id that any live process of yours holds is never started. Two copies of one
  session would both act.
- **A session comes back with the permission mode it had.** If it ran with permission checks switched off, it comes
  back that way, with nobody watching, and the opening message gives it one turn. You chose that mode when you
  started it; Tardigrade does not widen it. If you do not want sessions to act by themselves after a restart, use
  `"wake": "idle"`.
- It never ends a session, never types into one, and never answers a dialog for you. If a revived session stops at a
  question, it waits there for you.
- It never stores a token or a key. For a login token it keeps the variable's name and an 8-digit one-way
  fingerprint, and uses it to check that a session came back on the same login. Where sessions on one machine use
  different logins, a session with a token comes back silent, and if its login changed it is listed as "needs you"
  with nothing run on the wrong account.
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
| `skip_tmux_sockets` | `[]` | tmux socket names to leave alone, for when another tool already brings those sessions back. One restorer per session. |

The record is `~/.config/windstorm-tardigrade/lanes.json`. The log is
`~/.local/state/windstorm-tardigrade/tardigrade.log`.

## Tested

Proven:

- **Two real reboots**, 2026-11-21, an Ubuntu 24.04 laptop, Claude Code 2.1.323: one clean restart and one sudden
  reset with no shutdown. Three sessions were running: one with permission checks on, one with them off, and one in
  a plain terminal window. Both times all three came back as the same sessions, with their names and folders, in
  under three minutes, with nothing typed by anyone. After the reset the record on disk was intact. A second tool
  restored a fourth session on the same machine in the same second, and neither touched the other's
  (`skip_tmux_sockets`).
- **Opening windows** on that laptop's real desktop: three hidden sessions, three windows.
- **Acting on the opening message**: one real session on Linux, Claude Code 2.1.330. It was recorded, ended and
  brought back, and it read its notes and answered.
- The census on a shared Linux machine with 98 sessions: a quarter of a second, and its list matched a separate
  tool's list, 98 of 98.
- 28 tests on Linux with a stand-in for `claude`, run on every push with Python 3.10 and 3.13: record, revive, never
  a second copy, once per boot, a clean environment, the census, and the rules under Safety.

Not yet proven:

- **A session acting on the opening message after a real reboot.** On the test laptop the account had reached its
  usage limit, so each revived session stopped at Claude Code's own "limit reached" question and waited there. The
  census showed all of them as "waiting for you". That is the right behaviour, and it is not the same proof.
- **With nobody logged in at all.** The boot service started before any remote login, but that laptop also logs into
  its desktop by itself, so lingering alone was not isolated.
- **macOS**: the launchd agents and the FileVault notice.
- **Herdr** tab re-attach.
- Sessions pinned to different accounts by a token: proven with the stand-in only.
- **Windows** is not supported. Under WSL, use it inside the Linux system.

Two things the real machine taught us: Claude Code asks "do you trust this folder?" for a new folder even when
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
