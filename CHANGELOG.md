# Changelog

## 0.1.10.4 (2026-11-30)

- **After a boot run that came too late, your own revive lets the record go on (T138).** Where the boot service
  ran on a machine that had been up for longer than `boot_window_min` (40 minutes), it did nothing, and from then
  on the record job was skipped for the rest of that start, also after a revive by hand: a session started in
  that time was not recorded, while the census said "the next record takes it". Seen on 2026-11-26 on a stock
  Ubuntu 24.04 virtual machine (systemd 255) with this version's work before the change: 5 record runs skipped in
  24 minutes, 2 of them after a revive by hand. Now a revive by hand that is no dry run, has no `--only` and
  reaches its report outside that window counts as this boot's revive, and the next record writes. It does not
  count inside the window, where the boot run is still to come, and never over a record file that is not whole:
  then the run says in one line that it is not counted as this boot's revive, the record job stays skipped and
  that file is left as it is. While the record waits, the census says that it waits, and the step,
  `tardigrade revive`: of a session that is not on the record, and over sessions started new under a new id.
- **Two limits of lingering off, said where setup and the README say who has to log in.** Until 0.1.11 the boot
  window is counted from the machine's start and not from your login: after a first login later than 40 minutes,
  nothing comes back by itself, and the step is `tardigrade revive`. On a Mac the gate's line was seen at a
  direct run of the boot gate on a Mac up 100 hours (2026-11-26, macOS 27, no restart made); the boot job's own
  run at a late login was not seen, and the step after it was not typed on a Mac in that state. And with
  lingering off the sessions that came back by themselves end soon after your last login on the machine ends,
  unless something you started in a login is still running, and the next login brings nothing back. The step
  is `tardigrade revive`, first at that login, before you start another session: once one is running,
  Tardigrade can take the ended ones as closed by you within a few minutes, and revive then no longer brings
  them back. After that no command of this version brings them back (read in the code; `revive` and
  `revive --only` with the name and with the session id were typed, and none did); the census still lists
  them under DOWN with their folders. When sessions end this way, the system lists your account's service
  manager as failed until your next login. Lingering on keeps the sessions. Seen on 2026-11-26 on that
  virtual machine, lingering off, stand-in sessions throughout: with nothing else of the account running,
  the sessions were gone 15 s after the last login ended, and what was still running of them at the end of
  the service manager's stop time was killed; a command a login had left running kept them until it ended,
  and a session started by hand in a tmux server of its own kept them too; the step typed before another
  session was started brought back what the logout had ended (three accounts), and in one of them they then
  stayed over the next logout (watched for three minutes, seen once); typed after a record run with one
  session of the person's own running, it brought nothing back (two trials); where three sessions, all of
  the record, had ended at one logout, it brought all three back even then (seen once). Not seen: three or
  more ended sessions that are half of the record or less; how long sessions brought back by the typed step
  stay; a real Claude Code session in any of this.
- **When to run setup again after an update, said in the README and in the skill.** While your sessions are
  running, or after `tardigrade revive`; never between a restart and their return. Until 0.1.11, setup typed
  in that gap with one session of yours already running lets the others go, and neither the boot run nor
  revive brings them back: seen once on 2026-11-26 on that virtual machine, lingering off, stand-in sessions.
  Not seen: setup typed there with no session running.
- **Type each option exactly as the skill's table shows it (T156, #166).** This version passes over a word
  after the command that it does not read, and runs the command without it. So a mistyped `--dry-run` with
  `revive` starts the sessions for real (with `record`, `open`, `setup` and `uninstall` the same is read in the
  code), `trust on --dry-run` switches trust answering on (`trust` has no dry run), and `revive --only` with no
  name after it, or `revive --only=NAME`, starts every session that is down, not one. Seen on 2026-11-26 with
  stand-in sessions, not on a machine. Row T156 is due in 0.1.11: there the command names the word, does nothing
  and ends with exit 2.
- **A home folder whose path holds a space, and every other path a service file reads in its own way (T132).** Up
  to 0.1.10.3 setup wrote two paths into its two service files as they stood: the Python that ran setup, and its
  installed copy, which lies under the home folder. A service file reads some characters in its own way, so a path
  that holds one of them was not read as one whole value. In a home folder whose path holds a space, the boot
  service and the timer did not start Tardigrade, while setup said it was set up and the census said sessions come
  back by themselves. Seen with 0.1.10.3 on a stock Ubuntu 24.04 virtual machine (systemd 255). Now setup writes
  each of the two paths so that the service manager holds it as one whole value. The same start lines, placed by
  hand in unit files on stock virtual machines with systemd 237, 239, 245 and 249 (Ubuntu 18.04, AlmaLinux 8.10,
  Ubuntu 20.04 and 22.04), were held by each manager as on 255, and the record service started there with each
  path whole; setup itself was not run on those four.
- **A path the service file cannot carry is refused before the first write.** On Linux that is a Python whose
  path holds a double quote, an apostrophe, a backslash or a dollar sign: the service manager's start line has no
  way to hold one of these in a program's own path. On Linux and on a Mac it is a Python or a home folder whose
  path holds a control character (a line break or the like). Setup says so in one line that names what it found,
  gives the command that runs it with another Python, and installs nothing: no file is written. Where the path
  holds a backtick, a dollar sign before a parenthesis, or a character a line breaks on or a terminal acts on, the
  line shows the path changed (a question mark for each) and says so. The tool's older lines that show a path
  show it as it stands (T135, due 0.1.11).
- **Setup and the census read back what the service manager holds.** "Enabled" and "active" say that a service is
  there, not what it would start. From this version on, "is set up" and "Comes back after a reboot: yes" are said
  only after the service manager's own list of each service's start line has been asked (over its bus, with
  `busctl`) and is, word for word, what setup wrote. What setup wrote is its own record in the settings
  (`service_python`, `service_script`), never what the terminal that asks happens to have as its home or its Python.
  If the manager holds something else: "NOT set up" (exit 1), and the census says NO. Where the reload that setup
  asks of the manager after writing the files did not answer 0, setup says that too, and the step is to run setup
  again. If the list cannot be asked (no `busctl`, or no answer in 10 seconds): "could not read" (setup: exit 3),
  with one step by which you see it yourself. A setup made by an older version kept no record: after the update the
  census says "could not be read" and that the step is to run setup again, except where the older service file's
  line holds only plain words, which every reader takes alike.
  The census reads the boot service as the service manager holds it. A service file damaged after that (seen once:
  one cut short inside its start line) still reads as yes until the service manager reads its files again, at the
  latest at the next restart; that restart brought nothing back, and the census then said no. Run setup again after
  anything has touched those files. A setup that ends in an error while it writes its copy (seen: a setup stopped by
  a file-size limit) can leave a cut copy that the census still reads as yes, before and after a restart that brings
  nothing back; after any setup that did not end with its set-up line, run setup again. A disk that is full at a
  restart (seen: the disk that holds the state folder, with no block free) lets the boot run end before it brings
  anything back, and the census still reads yes; free space there, then run `tardigrade revive`. All three were seen
  on 2026-11-26 on a stock Linux virtual machine with stand-in sessions.
- **What is yours on a service is named as yours, and left as it is.** A drop-in file of your own on one of the two
  services is never changed, and never a fault. Environment entries of your own are named by the variable's name
  only, never a value (a value may be a password). A start line of your own beside the one setup wrote is counted,
  never shown, and Tardigrade stays set up as long as the manager's list holds setup's own line once, as written
  and first. If a drop-in of your own changes that (setup's line gone, changed, held twice or not first), setup says
  "NOT set up" and that the file is yours; it does not send you to uninstall. It says what to do (change or remove
  a drop-in that is not meant, then run setup again), and gives the service manager's own listing of that service
  and its drop-in files (`systemctl --user cat` and the service's name) as the last thing on a line of its own;
  the census does the same. The census says NO only where it read that setup's line is not held, or held changed.
  That NO is more than it read: the list differs, and what the held line starts was not read. It errs to the
  safe side (T136, due 0.1.11).
  Where the line is held as written, only not once and first, it says "not known", neither yes nor no, and names
  the place or the count: Tardigrade read the manager's list, not what the service then does. Recorded on stock
  virtual machines with systemd 237, 239, 245, 249 and 255, with drop-in files of made-up values: each manager's
  answers had the same form.
- **On a Mac** the two launchd files hold each argument as a string of its own, and no path stands in a line a shell
  reads. Where launchd answers and this version does not find a job's arguments in its answer, setup and the census
  say so, and the step is to tell the maintainer that line and the macOS version, not to log in at the Mac's screen.
- The line that says how to switch lingering on gives the account's name as one shell word, and takes it from the
  account itself, not from the terminal's variables.
- Where systemctl gives no answer, the step "log out and in" stood beside the words "this login has no systemd user
  manager to ask". On a virtual machine one was running and the shell could not reach it, so the words now say
  that: "this shell cannot reach the systemd user manager" (T133). The step itself has not been walked (T134,
  due 0.1.11).

## 0.1.10.3 (2026-11-27)

- **A # in a name or a folder no longer changes what comes back.** tmux reads a # in a session's name, a pane's
  title and a folder as the start of one of its own formats. Up to 0.1.10.2 Tardigrade handed these to tmux as they
  stood on its record. A session that ran in a tmux session whose name holds a # before certain letters, or two # in
  a row, was not brought back after a restart: the revive gave up and left a tmux session under a changed name
  (laneork for lane#Work), while the other sessions came back. A session in a folder with a # came back in its own
  folder, but tmux held a changed folder for its tmux session; and the waiting screen's title, made from the
  session's own name, was changed the same way. Seen with 0.1.10.2 and tmux 3.4 in a test with stand-in sessions:
  three of ten sessions not brought back. Now each of these is handed over so that tmux holds it letter for letter,
  and such a session comes back as itself: under its own tmux name, in its folder, its own name in the title. The
  folder goes over with each # doubled, which tmux gives back as one (every tmux tried reads a folder that way: 2.6,
  2.7, 3.0a, 3.2a, 3.4, 3.7c and 3.8). For a name and a title it depends on the tmux: 2.6 leaves them as they stand,
  2.7 and later read them. Tardigrade knows no versions; it asks the tmux it finds. Once in a run, and only when a
  name or a title with a # is to be handed over, it makes one tmux session of its own under a made-up name (tmux
  then shows it as tardigrade-asks-# or tardigrade-asks-##; it holds a sleep that ends by itself), reads back what
  tmux holds, and ends that session. Like a waiting screen, it is a tmux session of Tardigrade's own making, in the
  same run; it ends no other. If tmux cannot be asked (a tmux session of that made-up name is already there, which
  is left alone; tmux made no session, with its own words if it said any, and else that it said nothing, gave no
  answer in 30 seconds or could not be run; tmux held the name as something else), a session whose tmux name holds a
  # is not started under a guessed name: its line says NOT STARTED and which of the three, the run asks again at
  each try, and the other sessions come back. If the run gives up on it, its give-up line points to the README's
  edge on starts that failed, which says how one such session is brought back by hand. A title never stops a start:
  with no answer it goes over without its #. A run with no # in any name, title or folder asks nothing and does what
  0.1.10.2 did. Seen with this version and stand-in sessions, in its six new tests: on tmux 3.4 in the whole suite
  on a Linux workstation, and once each on stock virtual machines with tmux 2.6 (Ubuntu 18.04, with Python 3.8 added
  from that release's own archive), tmux 2.7 (AlmaLinux 8.10, Python 3.9) and tmux 3.0a (Ubuntu 20.04, Python 3.8).
  They also run on every push with the whole suite, on the tmux of the test image (package 3.6a-2ubuntu0.2 when this
  was written, which answers "tmux 3.6" when asked for its version). The first push of this version's line ran the
  suite there four times, and one of the eight runs of the two tests that walk every place was red: everything those
  tests read of Tardigrade had held, and the test's own control, which comes after, waited 40 seconds for a sign
  from tmux that did not come. The control was changed (in the test only: the client that asks tmux now stays until
  the sign is there), and on the next push eight of eight were green. Three of the six tests hold what comes back
  and what is said when tmux cannot be asked; a fourth walks each of the three causes, and a fifth a start that
  fails for a cause of its own once tmux could be asked. And through a real restart of a stock Ubuntu 24.04 virtual
  machine (tmux 3.4), with seven stand-in sessions, a # in a tmux name, a folder or their own name: the boot
  service's own run of this version brought back seven of seven as they were, each once and in its own folder, where
  0.1.10.2 in the same world brought back four of seven; and seven of seven again on a machine where 0.1.10.2 was
  set up first, this version set up over it, 0.1.10.2 over that and this version once more, the seven sessions
  running through all of it. When the session it asked with was the only one on that tmux server, tmux ends the
  server with it, and a start that reaches the server just then fails (tmux 3.4 alone: in 2 of 300 tries in one
  trial and in 1 of 300 in another; and once in this version's own test run): Tardigrade waits until that server is
  gone, 3 seconds at most. A sixth test holds that with a stand-in whose server takes a second and a half to end.
  Not seen: a real Claude Code session with such a name; a Mac; a tmux that answers no call at all. Found on the way
  and not changed here: a revive in which a start failed still ends with exit code 0 in this version (T121, due
  0.1.11). Nothing else changed. Still on 0.1.10.2 and met it? Give that tmux session a name without a # while it
  runs, and from the next record on it comes back (the README of 0.1.10.2 has the line) (T119).

## 0.1.10.2 (2026-11-25)

- **One folder with an odd name no longer stops every session from coming back.** Up to 0.1.10.1, when one recorded
  session was in a folder whose name is not UTF-8 (a name written long ago in another encoding), the revive after a
  restart ended in an error before it started anything: no session came back, the ones in ordinary folders
  included, and census ended in the same error. Seen with 0.1.10.1 and stand-in sessions on a stock Ubuntu 24.04
  virtual machine through a real restart: 0 of 3 back by the boot service and 0 of 3 by hand, each time with
  "UnicodeEncodeError" and the words "surrogates not allowed". Now such a name ends no command: on the screen and in
  the log its odd byte is written as a backslash form, every other session comes back, and that session too, in its
  own folder (seen with this version on the same machine through a real restart: 3 of 3 back, the boot
  service ending with success, census listing all three). On a Mac's own disk such a folder cannot be made
  (macOS 27, APFS: "Illegal byte sequence"). Whether Claude Code itself can be in such a folder: version 2.1.332,
  not signed in, refused to start in one (seen once each: at its screen, and in print mode); one that was
  running when its folder was renamed to such a name kept running there (seen once, at its first screen). Not
  seen: a signed-in session in such a folder, and whether Claude Code records that folder for it. A process
  that is no session, with such a byte in its command line, ended nothing (3 of 3 sessions back). Nothing else
  changed. Still on 0.1.10.1 and met it? A session in an ordinary folder comes back by its name, one at a time:

      tardigrade revive --only "Name"

  Not cured by this version, due in 0.1.11 (T114): a session whose recorded NAME holds half of an emoji (a lone
  surrogate) still ends a revive, and no session comes back (seen in a test with stand-in sessions, not on a real
  machine); odd characters in other names and in what other programs print are not read yet either (T115).
  Found while this version was tried, and not cured by it either: a session in a tmux session whose name holds a
  backslash is not brought back, while the others are (T116; seen on a stock virtual machine with tmux 3.4); and
  under a language setting that is not UTF-8, a session whose folder name holds a character outside ASCII comes
  back in the home folder, not its own (T117; seen in a test with a made locale, not on a real machine; 0.1.10.1
  brought back none there). And a session ended with /exit, then resumed, whose every line after that holds the
  character U+2028 as it is, is skipped by the revive as ended by its owner; a tmux session name that holds
  U+2028 or NEL comes back cut at it (T118; seen with stand-in sessions and planted lines on a stock virtual
  machine; that Claude Code writes the character as it is was counted in two real conversation files). The
  README's known edges say what helps.

## 0.1.10.1 (2026-11-24)

- **Census no longer says "comes back after a reboot: yes" when nothing would.** Up to 0.1.10 the answer was read from
  the setting and lingering alone, never from the boot service: with the boot service off, its units gone or the
  installed copy missing (an uninstall cut part way, a service switched off by hand, a home copied without its units)
  census still said "yes, by itself" (seen on 0.1.10 in a scratch home holding only the setting). It now reads the
  installed copy, the boot service (enabled) and the record timer (active), on a Mac both launchd jobs (loaded), at
  that moment: NO names what is missing and says to run setup; "could not be read" when systemctl or launchctl gives
  no answer, never yes, with the step to take (Linux: log out and in; Mac: log in at the screen; then setup again). Setup's "is set up" is the same read, made as its last act: exit 0 only when it passes, 1
  with what is missing, 3 when it could not be read (T93).

## 0.1.10 (2026-11-24)

- **Set up under a wide-open umask? Run setup again.** Up to 0.1.9, setup started with umask 000 (as some job runners
  start every job) made the installed copy and the three units at mode 666 and the folders it created at 777
  (`~/.local/share/windstorm-tardigrade`, `~/.config/systemd/user`, `~/.local/state` and the folders above them it had
  to make). The boot service runs that copy as you, so another account that could reach those folders could have
  changed what runs at your next boot (seen in a scratch home on 0.1.9; no separate notice for 0.1.9, since it takes
  that umask AND a home another account can enter). From 0.1.10 the tool adds 022 to whatever umask it is given, and
  setup takes group and other write off its own copy, units and install folder when an earlier run left them open
  (T80).
- **The folders above are named, not changed.** A file is only as closed as the folders above it. Setup, the boot run
  and census walk every folder from your home down to each thing of Tardigrade's that runs or decides what runs (the
  copy, the units, the settings folder, and the state folder where the start files are written). A folder it cannot
  know it made, whose mode bits and group members let another account reach it and write it, is named in a numbered
  line with the one command that closes it (`chmod go-w <folder>`); census repeats it until it is closed. A folder of
  your own private group with nobody else in it is not named. A folder another account owns is named with its owner
  and no command. What is read is the mode bits and the groups' members; access lists are not read. Seen on a stock
  Linux machine, and on two Macs in test accounts. On macOS 27, logged in at the screen: setup (exit 0), census and
  uninstall. On macOS 13, with no login at the screen: setup stopped (exit 1, launchd took no job), and census and
  uninstall ran. On both, the walk named nothing at a home of 700 and named a folder opened to the group every Mac
  account is in; an open launch agents' folder was not steered (T80).
- **Only what Tardigrade wrote.** Setup, revive and uninstall overwrite or remove only files Tardigrade wrote, known by
  their own marks (seen on 0.1.9 in a scratch home: setup wrote over a person's own `tardigrade.py`, uninstall removed
  it and a person's `__pycache__` folder whole, a revive removed a person's folder named `lock` and a person's file in
  the start-file folder). Setup now stops, names the file and installs nothing; a folder named `lock` holding something
  else stops a revive, which says so; nothing deletes a folder whole any more (T79).
- **Uninstall goes to its end.** A file of yours in one of Tardigrade's folders no longer makes it a failure: everything
  of Tardigrade's goes, what stayed is named as yours with why each folder stayed, the exit code is success, and the
  last line says Tardigrade is off this computer. 0.1.9's README said, for the settings, record and log, "to remove them
  as well, delete `~/.config/windstorm-tardigrade` and `~/.local/state/windstorm-tardigrade` afterwards". That would
  take a file of yours in them too, so from 0.1.10 what stayed is yours, to keep or delete after you look inside, and
  no folder is offered for deleting whole (T79).
- **`TARDIGRADE_TESTING` alone changes nothing.** The test behaviour (shorter waits, stand-in facts) now takes the
  setting AND a marker file the test suite writes in the scratch home it made. With the setting alone, a run says on
  its first line that it found the setting and ignored it. The repository's `environment.json` lists every environment
  setting the tool reads, as a person's, a fact from the host, or a test setting; a test holds the list to the code (T78).
- The README's clone line names the release tag, and a test reads every clone line (T76). Nothing Tardigrade shows or
  runs removes the plugin catalog, which would uninstall every plugin from it (T77). The test suite runs under the
  skill family's clock shift at 0, 1, 7 and 30 days ahead before each tag; the tool has no clock offset of its own (T75).
- Moved to 0.1.11, to cut 0.1.10 small for T80: T12, T14, T19, T27, T30, T32, T47, T51, T54, T56, T57, T58, T59, T60,
  T61, T62, T63, T64, T69, T70, T71, T72, T73 and T74. The known edges of 0.1.9 for T47, T54, T56, T57 and T70 hold
  until 0.1.11.

## 0.1.9 (2026-11-23)

- **If you installed through the plugin system** (`claude plugin install`) at 0.1.8 or 0.1.8.1, you may still be
  running that version. The plugin's manifest (`.claude-plugin/plugin.json`) said 0.1.7 in 0.1.8, 0.1.8.1 and
  0.1.8.2, and Claude Code keys a plugin's folder on that label. Seen on a stock Ubuntu VM with Claude Code 2.1.330:
  installed at 0.1.8, then `claude plugin marketplace update` and `claude plugin update` said "already at the
  latest version (0.1.7)" and kept 0.1.8's script, so running setup again installed 0.1.8 again. From 0.1.9 the
  manifest says the script's version, and the suite fails when they differ (T50); with the label changed, `claude
  plugin update` was seen to update. Then run setup again: it refreshes the copy the services run.
- **A read that failed is "cannot tell", never "nothing runs"** (T52, T53, T55; seen in a probe on 0.1.8.2, each read
  made to fail in turn). In every earlier version a process-list read that failed or timed out came back as an empty
  list. Then:
  - a restore could start a second copy of a session that was already running, for example one started by hand;
  - census said "Running: 0 ... down: N" while those sessions ran;
  - when tmux's pane list failed, record rewrote where each session lives.
  Now a restore starts nothing in a pass whose read failed and reads again at the next. Census and `report` say they
  cannot tell and give no counts. A restore's count and `on_done` say "unknown". The record is kept as it was.
  A stale tmux socket left after a reboot still counts as no panes. Only the tmux servers the record depends on count:
  each recorded session's, tmux's default, `loose_socket` and `tsv_socket`. A record skipped for an hour or more is
  named by census ("RECORD NOT WRITTEN since ..."). A restore whose first read keeps failing for `retry_min` starts
  nothing; this boot's report and `on_done` say so (`TARDIGRADE_CANNOT_TELL`). Each start is decided on a read taken right before
  it: with `load_max` set, the load wait now comes before that read, not after it, so a session started by hand during
  the wait is seen, not started again (T52, found in a trace on a loaded machine).
- Known edge until 0.1.10 (T54): a running session whose own session file cannot be read, and that was not started
  with `--resume` of a recorded id, is counted by record as ended, so the next restart does not bring it back.
- Known edge until 0.1.10 (T56): with Claude Code installed through npm at 2.1.112 or older (run by node), a session
  started by hand cannot be seen as running until it has loaded, and a restore can start a second copy of it. Update
  Claude Code (2.1.113 and later ship the native program); start nothing by hand while a restore runs. The occasional
  G4.4 failure in CI was this window, seen through the test's stand-in: the suite now starts its stand-in so that it
  is named "claude" from its first instant, as the native program is.
- Known edge until 0.1.10 (T57): a session started on an account has been seen to lose its label from its record
  row, so the next restart started it on the machine's own login without a word. After starting such a session, check
  that its line in `revive --dry-run --all` ends in `[account NAME]`.
- Not in 0.1.9, moved by the skills' department head: T12, T14, T19, T27, T30 and T32 to 0.1.10 (the checks before a
  planned reboot), T11, T16 and T22 to 0.1.11. What you can meet before then is in the README: a session moved
  between logins at a restart, a session started with a login token in its environment, a variable set by hand,
  and sessions that come back with no window.

- Known edge (T41, G1.15; fix due in 0.1.10, T47): of a process in a `skip_cwd_prefix` folder, Tardigrade's own
  code reads only its folder. On Linux the system's `ps`, which it runs for the process list, opens more of every
  process on the machine (environment and arguments too; seen with strace, procps-ng 4.0.4) and passes back only
  process id, parent, user, state and program name.

## 0.1.8.2 (2026-11-22)
A fix to how a session is started at boot (G2.49, T48). **0.1.8 and 0.1.8.1 did not bring sessions back on a Linux
machine whose service `PATH` lacks the folder Claude Code installs into** (`~/.local/bin` for its own installer), when
no shell startup file adds that folder: every start said "claude: command not found". A stock Ubuntu machine is
such a machine. Since 0.1.8 each start at boot replaces its waiting screen through `tmux respawn-pane`, and tmux gives
that pane the `PATH` of whoever asks, which was the boot service. Now that pane gets the same clean environment as a
new one. Its `PATH` is the `path` setting exactly as written, or, with none, a standard `PATH` plus `~/.local/bin`
first and then every other whole folder of the `PATH` of whoever runs the restore (at boot: the boot service), so a
session never has less than before.

One more change, from the same line: with no `path` setting, a session started in a new pane (not one replacing a
waiting screen) also gains those other folders now. Before, a new pane had the standard `PATH` plus `~/.local/bin`
only. The default still comes first, so nothing is lost; a `revive` typed by hand passes on the folders of the shell
it is typed in.

Note added 2026-11-22, after the release: the plugin manifests of 0.1.8, 0.1.8.1 and 0.1.8.2 named an older version
(0.1.7). Claude Code keys an installed plugin on that label, so for a plugin install `claude plugin update` reported
"already at the latest version" and kept the old script. Corrected on main on 2026-11-22: the manifest now says
0.1.8.2. The tagged commit v0.1.8.2 itself still carries the old label; the script is the same.

Note added 2026-11-23, a known edge of 0.1.8.x found in a probe: a failed read of the process list came back as an
empty list. On a machine so loaded that `ps` fails or does not answer within 30 s, census can then report running
sessions as down, and a restore can start a second copy of a session that was started by hand meanwhile; with
`load_max` set, a session started by hand during a restore's load wait can also be started again. Fixed in 0.1.9
(T52, T53). Until then: start nothing by hand while a restore runs, and check with your terminal multiplexer
(`tmux ls`) before believing "down".

## 0.1.8.1 (2026-11-22)
A fix to what 0.1.8 counts as a session (G1.14, T40). A `claude -p` run, which Claude Code marks "interactive"
in its status file, was written down as a session when it ran in a terminal or a tmux pane, and a restart could
have started it again as an interactive session. Now a print-mode run, a run through an SDK, a process whose
input is not a terminal, and a process started inside another Claude session are never recorded, listed or
started; they are counted as helpers. "Inside another session" means a claude among its parents below the tmux
pane, Herdr, terminal window, editor or ssh login it lives in: a session in a Herdr server or a terminal that
someone opened from a Claude session is still a session.

Known edge: after upgrading on a machine where no session is running, look at the census before the next
reboot. A record that finds no live session keeps the record before it, so a print-mode run that 0.1.8 wrote
down can stay on it, and a reboot would start it. With any session running, the next record clears such a
row by itself (it moves to the down list, which is never started). 0.1.9 adds a guard (T43).

## 0.1.8 (2026-11-22)
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
- **A session its owner ended with `/exit` stays ended** (G2.36), and is not counted in a crash. Claude Code
  2.1.330 often writes no `/exit` into the conversation (seen on two real machines), so the end of its prompt
  history is read too, for the sessions on the record only. Of what a person typed there, only the time and
  whether it is exactly `/exit` or `/quit` is kept, and nothing of it is logged or shown. If the history cannot be
  read, nobody is taken as ended by it, and the log says so.
- **`census` marks as "started by hand" only what was** (G3.7). On a real machine it marked two sessions that share
  a name, and sessions the restore itself had started new under a new id. Now a session in a tmux session the
  restore made, or with any id coming back under its name, is never marked. While a restore is still watching
  sessions that are all back, `census` says so instead of speaking of others on their way.
- **`on_done` hands on "needs you" as its own count** (G2.38), `{needs_you}`, not as `failed`: on a real Mac a clean
  restore with one session on another login was handed on as one failure. The record's copy (`lanes.json.prev`) is
  0600 like the record (G2.42), and `uninstall` leaves no compiled copy behind.
- **A lane started new under a new id is not listed as down** (G3.16). On a real Mac, `census` said "down: 14"
  for a clean restore until the next record; now it lists those lanes as started new, with both ids.
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
  And a limit belongs to the login: once a note just typed on a login meets the limit (limit words that appear
  after it; an old limit reply in a conversation or on a resumed screen holds only its own session), no other session on that login gets a note in that
  run. A `wake_text` that reads like a limit notice is refused (setup names the words; the default note is used), so many sessions sharing one login do not all run stale notes at once at the reset.

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
