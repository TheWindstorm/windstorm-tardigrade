# Changelog

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
