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
names, folders and counts, never message text. Run it with `python3 -I`. It runs on Python 3.9 or newer; on an
older one every command stops at once, says the version it found, and makes nothing: show that line as it is.
After setup, the installed copy is
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
| A row takes the login its session runs on (on the user's word only) | `record --accept "Name"` |
| Check before a planned reboot | `revive --dry-run --all` |
| Have the trust question answered after a restart | `trust on` (see below) |
| Switch it off | `uninstall` |

Type each option exactly as it stands here: until 0.1.11 the tool passes over a word it does not read and runs
the command without it, so a mistyped `--dry-run` acts for real, `trust on --dry-run` switches it on (trust has
no dry run), and `revive --only` wants the name as its next word.

## Setup

1. Run `setup --dry-run` and tell the person what it will install: a boot service and a 5-minute timer for their own
   user. No administrator rights.
2. Run `setup`. If tmux is missing it says so and stops: give the person the install line and wait. Setup says
   "is set up" only after reading the boot service, the timer and the copy themselves, and the start line the
   service manager holds for each service, word for word. "NOT set up" (exit 1) names what is missing or what
   differs; "Setup could not read whether it is set up" (exit 3) means systemctl, launchctl or the manager's own
   list gave no answer: it is neither set up nor not. Show its words and its one next step; never call it set up.
   A line that begins "Yours, not Tardigrade's" names a setting of the person's own on a service: show the line
   and the tool's own step, change and remove nothing yourself, and never suggest removing a setting the tool only
   named as theirs. Where the tool's own step says to look at a drop-in file, pass that step on as printed.
3. Show the last lines it prints, word for word. They say who has to log in after a restart before sessions can
   come back (lingering on Linux, FileVault on a Mac).
4. If lingering is off, give the person the one command it prints. **Do not run it yourself.** It needs `sudo`, and
   it decides that their processes keep running when they are logged out. That is their call.
5. Run `census` and show it.

After the plugin is updated, run `setup` again so the installed copy matches: while the person's sessions are
running, or after `revive`. Never run it between a restart and the sessions' return: until 0.1.11, setup typed in
that gap with one session already running lets the others go, and neither the boot run nor `revive` brings them
back. If the machine has just restarted, run `census` first, and run `setup` only once the sessions are back: by
themselves, or by `revive` as below.

If setup says the installed copy is a newer version than this script, it has stopped and changed nothing. Show its
words. **Never add `--downgrade` yourself**: moving a machine back is the person's choice, in their own words. Census
says when the plugin's version differs from the installed copy's, when Claude Code holds the plugin more than once,
and when Claude Code removed the plugin while what setup installed still runs: show that line too. Do not uninstall
either plugin yourself: which one stays is the person's to say.

## Census

Run `census` and show the table as it is. Then say, in one or two lines, what stands out: sessions that are hidden
and have been quiet for days, and sessions that are down.

If the census says it **cannot tell** (the process list or tmux could not be read), show that line and nothing more:
do not call any session down, do not run `revive` and do not start any session by hand on the strength of it. Run
`census` again in a minute; if it still cannot tell, say so to the user and let them look with `tmux ls`.

If the census has a **CANNOT TELL** list, or a line that a session file could not be read, show it as it is. Those
sessions are not down: do not call them down, and do not start one with `revive --only` unless the user says it is down.

If the census has a **Login differs** or **Login changed** line, show it as it is. `Login changed` is news: the user
started that session again on another login, and the record followed. `Login differs` needs the user: a session that
Tardigrade started came up on another login than its row has. Do not run `record --accept` yourself: which login is
the right one is the user's to say. Ask them to look at the login the session shows, and run it only on their word.

If the census says **not known** after "Comes back after a reboot", say exactly that, neither yes nor no; show the
line and the step on the line after it as printed.

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

Before a planned reboot, run `revive --dry-run --all` and show it: it lists the line that will start each
recorded session, and starts nothing. A `VAR=value` word in the person's own start command is shown as
`VAR=(set)`; a value given as a flag's word is shown as it is. Do not add `--start-file` unless the person asks
for it: that prints the start file whole, values included, into this conversation.

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
says which it is. There is no dry run of `trust`: `trust` alone says which it is and changes nothing.

## Switch it off

Run `uninstall` and show what it prints, word for word. It turns off the timer and the boot service and removes
the units and the installed copy. A file Tardigrade did not write that sits in one of its folders is the person's: it
is kept and named, with why its folder stayed. That is not a failure; the exit code is success and the last line
says Tardigrade is off this computer. Exit 1 means something of Tardigrade's could not be removed or still runs, and
exit 3 that systemd or launchd gave no answer: show the lines, give the one command they name, and do not say it is
off. If it says it is waiting (for a revive, or for the boot service to end), let it wait. If its first line says
"NOT off this computer, and nothing was removed" (something of Tardigrade's was read still on) or "could not read
whether everything of Tardigrade's is off, and removed nothing" (systemd or launchd gave no answer, so it is
neither off nor known to be on), believe it: show what it read of each unit or job and its one next step as
printed, and remove nothing by hand. If it says that this run stopped the record timer or took the record job off,
say that too: no record is taken until the next boot or login, or until the setup line it prints is run.

Never delete, and never offer to delete, anything the uninstall kept (the person's files, the record, the log, the
settings, a folder that stayed) unless the person asks for it in their own words in this conversation.

Never run `systemctl --user stop`, `restart` or `kill` on `windstorm-tardigrade-revive.service`, before or after an
uninstall, and never tidy it away: a stop can end the person's tmux server and every session in it. The only hand
command for a boot service listed as failed is `systemctl --user reset-failed windstorm-tardigrade-revive.service`.

## What it never does

- It never starts a second copy of a running session.
- It never ends a session of the person's and never answers a dialog, except the trust question after the person
  has said `trust on`. (The only tmux sessions it ends are ones it made itself in the same run: a waiting screen,
  and the one that asks tmux how it takes a # in a name.)
- It types nothing into a session except its own wake-up message, into a session it started, once that session
  is idle.
- It never records a token or a key it finds in a session's environment: for a login token it keeps the variable's name and an 8-digit one-way fingerprint. What the person writes into `claude_command`, `account_command` or `start_command` themselves is copied as written into each session's start file (in Tardigrade's state folder, readable by them only); a key written there is in those files until the first revive after the session has left the record, and uninstall keeps that folder.
- It counts another user's processes by program name only; its own code reads nothing else of them (on Linux the system's `ps` it runs opens more of every process, until 0.1.11).
- It does not bring back timers, watchers or background commands. It tells the revived session they are gone.

## If you are a session that was just brought back

You will see a message that begins "Windstorm Tardigrade: this machine restarted". Tardigrade typed it; no
person did. Your conversation is intact.
Anything you had running in the background is gone. Read your handoff or notes if you keep them, check what was in
progress, start again only what is still needed, and carry on. If nothing was in progress, say so in one line and
wait.
