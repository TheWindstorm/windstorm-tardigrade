# Changelog

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
