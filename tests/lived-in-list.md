# The lived-in list (G5.19)

A stock machine's settings hold nothing of a person's, and a person's machine is full. Before each tag, after the
stock round, one round is run on the same stock machine with every line below in place. A line is added the day a
person's machine shows one this list lacks. A line marked (suite) is also planted by the suite itself, in the tests
named G5.19; a line marked (round) is in the lived-in round only.

## A tmux settings file

- base-index 1 (suite)
- a default shell and a default command (suite)
- remain-on-exit (suite)
- destroy-unattached (suite)
- a status line that runs a command (suite)
- a plugin-manager line (suite)

## A shell start file that

- prints (suite)
- asks with read (suite)
- starts tmux itself (suite)
- puts ~/.local/bin late on PATH (suite)
- runs a version manager's init (suite)
- sets noclobber (suite)
- ends the shell (suite)
- hands the pane to another program (suite)
- runs a program that does not end (suite)
- hands the pane to another shell (suite)

## The person's own units

- a service with Restart=always (round)
- a timer (round)
- an @reboot crontab line (round)

## The environment

- TMUX_TMPDIR (suite)
- an exported token (suite)
- CLAUDE_CONFIG_DIR (round)
- a LANG that is not UTF-8 (round)

## A Claude Code settings file with

- many keys (round)
- other plugins' hooks (round)
- its own status line (round)
- a real login (round)
- a plugin of another version beside this one (round)
- a real tmux older than this machine's (round)
