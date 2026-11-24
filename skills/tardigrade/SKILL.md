---
name: tardigrade
description: 'Windstorm Tardigrade brings every Claude Code session back after a reboot, crash or power cut (same conversation, name, folder, model and permission mode) and lists which sessions are running, which run with no window open, and which are down. Use when the user says "tardigrade", "tardigrade setup", "tardigrade census", "tardigrade status", "tardigrade revive" or "tardigrade open"; asks to restore, resurrect, reopen or bring back their Claude Code sessions, lanes or terminals after a restart; wants sessions to survive a reboot; or asks which Claude sessions are still running, running in the background, hidden or forgotten. Also use right after installing this plugin for the user, to run setup and show what it says. Not for reopening one conversation the user can pick with claude --resume, and not for restoring files or git state.'
license: Apache-2.0
---

# Windstorm Tardigrade

A tardigrade dries out, stops, and carries on when the water returns. This skill does that for Claude Code
sessions: after a restart each one comes back as itself, and `census` says what is running.

The whole tool is one script: `scripts/tardigrade.py`, two levels above this skill's base directory
(`<base>/../../scripts/tardigrade.py`). It uses only the Python standard library, talks to no network, and prints
names, folders and counts, never message text. Run it with `python3 -I`. After setup, the installed copy is
`~/.local/share/windstorm-tardigrade/tardigrade.py`.

If neither file exists, this skill was installed without its plugin. Get the script with
`git clone https://github.com/TheWindstorm/windstorm-tardigrade` and run `scripts/tardigrade.py` from that folder.

## Commands

| The person wants | Run |
|---|---|
| Switch it on | `setup --dry-run`, show it, then `setup` |
| What is running? | `census` |
| Windows for the hidden ones | `open` |
| Bring back what is down | `revive --dry-run`, show it, then `revive` |
| Bring back one | `revive --only "Name"` |
| Check before a planned reboot | `revive --dry-run --all` |
| Have the trust question answered after a restart | `trust on` (see below) |
| Switch it off | `uninstall` |

## Setup

1. Run `setup --dry-run` and tell the person what it will install: a boot service and a 5-minute timer for their own
   user. No administrator rights.
2. Run `setup`. If tmux is missing it says so and stops: give the person the install line and wait.
3. Show the last lines it prints, word for word. They say who has to log in after a restart before sessions can
   come back (lingering on Linux, FileVault on a Mac).
4. If lingering is off, give the person the one command it prints. **Do not run it yourself.** It needs `sudo`, and
   it decides that their processes keep running when they are logged out. That is their call.
5. Run `census` and show it.

After the plugin is updated, run `setup` again so the installed copy matches.

## Census

Run `census` and show the table as it is. Then say, in one or two lines, what stands out: sessions that are hidden
and have been quiet for days, and sessions that are down.

If the census says it **cannot tell** (the process list or tmux could not be read), show that line and nothing more:
do not call any session down, do not run `revive` and do not start any session by hand on the strength of it. Run
`census` again in a minute; if it still cannot tell, say so to the user and let them look with `tmux ls`.

Do not end, restart or change any session because of what the census shows. List them and ask. A hidden session may
be doing exactly what its owner wants.

## Revive

`revive` starts every recorded session that is not running. It never starts a session that is already running, so
it is safe to run twice. Always show `revive --dry-run` first when the person did not name one session.

A session comes back with the permission mode it had, word for word. If it ran with permission checks switched
off, it comes back that way and takes one turn by itself to read the wake-up message. Say this when the dry run
lists such sessions (`bypass` in the line). For silent returns, the person sets `"wake": "idle"` in
`~/.config/windstorm-tardigrade/config.json`.

The wake-up message is typed into a session once it is loaded and idle. A session that comes back to a question
gets no message; the log and the census name it.

After the starts, `revive` keeps looking for a few minutes and prints a count: LOADED, UP-NOT-LOADED and DOWN.
Show that line. UP-NOT-LOADED means a question is open on that session's screen: the person opens its window.

Before a planned reboot, run `revive --dry-run --all` and show it: it lists the exact line that will start each
recorded session, and starts nothing.

A line "Starting it new" means that session never had a conversation, so there was nothing to resume; it came
back as a new session with the same name. Nothing was lost.

If the census says sessions "vanished together", that was a crash. `revive --dry-run`, show it, then `revive`.

Lines that start with `needs you` name sessions that came back on a different login than they had before. This
happens only where sessions on one machine are pinned to different accounts. Such a session is idle and nothing
ran in it. The person closes it and starts it their usual way.

## The trust question

Claude Code asks "do you trust this folder?" on every start of a session whose folder is the home folder. After a
restart such a session waits at that question, and the census marks it `trust?`.

`trust on` makes Tardigrade answer Yes for a session it brings back, in the folder that session was already running
in. Run `trust on` only when the person asks for it in their own words. Before you run it, tell them what it does in
one sentence, as above. Never switch it on because it would be convenient. `trust off` undoes it, and `trust` alone
says which it is.

## Switch it off

Run `uninstall` and show what it prints, word for word. It turns off the timer and the boot service and removes
the units and the installed copy. A file Tardigrade did not write that sits in one of its folders is the person's: it
is kept and named, with why its folder stayed. That is not a failure; the exit code is success and the last line
says Tardigrade is off this computer.

Never delete, and never offer to delete, anything the uninstall kept (the person's files, the record, the log, the
settings, a folder that stayed) unless the person asks for it in their own words in this conversation.

## What it never does

- It never starts a second copy of a running session.
- It never ends a session and never answers a dialog, except the trust question after the person has said
  `trust on`.
- It types nothing into a session except its own wake-up message, into a session it started, once that session
  is idle.
- It never stores a token or a key, and counts another user's processes by program name only; its own code reads nothing else of them (on Linux the system's `ps` it runs opens more of every process, until 0.1.11).
- It does not bring back timers, watchers or background commands. It tells the revived session they are gone.

## If you are a session that was just brought back

You will see a message that begins "Windstorm Tardigrade: this machine restarted". Tardigrade typed it; no
person did. Your conversation is intact.
Anything you had running in the background is gone. Read your handoff or notes if you keep them, check what was in
progress, start again only what is still needed, and carry on. If nothing was in progress, say so in one line and
wait.
