#!/usr/bin/env python3
"""What decides that a run of the suite is green (G7.12, T108).

    python tests/run_whole.py           the whole of tests/test_tardigrade.py
    python tests/run_whole.py FILE      the same for another test file (the gate's self-test plants some)

A test, or the tool under test where a parent leaves after a fork, can end the test process with exit 0 before the
runner's summary and before the gate, which runs in that same process: such a run printed nothing wrong and ended 0.
So the run is a child of this file, and green is decided here, outside it. All of these must hold:
  - the child's exit is 0;
  - the runner's summary ("Ran N tests in ...") and its "OK" are there, and the gate's own line is the last line;
  - the finished run wrote its own result (a file only its last statement writes; a line a test prints is not it);
  - N is the number of tests the file defines, read from the file's syntax: every test method of every TestCase
    class, inherited ones too. unittest's "Ran N" counts a skipped test as run, so a test skipped on a condition of
    the machine does not change the count.
A run with a filter (-k, a test's name) decides nothing, and this file takes none.
"""
import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile

SUMMARY = re.compile(r"^Ran (\d+) tests? in \d")
OK = re.compile(r"^OK( \(.*\))?$")
GATE_LINE = re.compile(r"^gate: \d+ named test\(s\) passed in this run$")


def defined(path):
    """the number of tests a file defines, as unittest collects them, read from its syntax and never by loading it"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}

    def bases(c):
        return [ast.unparse(b) for b in c.bases]

    def is_case(c, seen=()):
        return any(b in ("unittest.TestCase", "TestCase")
                   or (b in classes and b not in seen and is_case(classes[b], seen + (c.name,))) for b in bases(c))

    def tests(c, seen=()):
        names = {n.name for n in c.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")}
        for b in bases(c):
            if b in classes and b not in seen:
                names |= tests(classes[b], seen + (c.name,))
        return names
    return sum(len(tests(c)) for c in classes.values() if is_case(c))


def verdict(code, text, result, count):
    """-> why this run is not green, one reason each (none: it is green)"""
    why = []
    if code != 0:
        why.append(f"the run ended with exit {code}")
    lines = [x.rstrip() for x in text.splitlines() if x.strip()]
    ran = [int(m.group(1)) for m in map(SUMMARY.match, lines) if m]
    if not ran:
        why.append('the runner\'s summary line ("Ran N tests in ...") is not there')
    elif ran[-1] != count:
        why.append(f"the runner says it ran {ran[-1]} tests; the file defines {count}")
    if not any(OK.match(x) for x in lines):
        why.append('the runner\'s "OK" is not there')
    if not lines or not GATE_LINE.match(lines[-1]):
        why.append("the gate's own line is not the last line of the run")
    if result is None:
        why.append("the finished run wrote no result: it ended before its own end")
    elif not isinstance(result, dict) or result.get("ok") is not True:
        why.append("the run's own result does not say it passed")
    elif result.get("ran") != count:
        why.append(f"the run's own result counts {result.get('ran')} tests run; the file defines {count}")
    return why


def main(argv):
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print("run_whole: it takes one test file at most and no other word: a filtered run decides nothing.")
        return 2
    path = os.path.abspath(argv[0]) if argv else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                             "test_tardigrade.py")
    count = defined(path)
    fd, res = tempfile.mkstemp(prefix="tg-run-", suffix=".json")
    os.close(fd)
    os.remove(res)              # the name only: the file is there when the finished run has written it
    flags = (["-I"] if sys.flags.isolated else []) + (["-B"] if sys.flags.dont_write_bytecode else [])
    run = subprocess.Popen([sys.executable, *flags, path, "-v"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=dict(os.environ, TARDIGRADE_RUN_RESULT=res))

    def hand_on(signum, _frame):        # a stop of this file is a stop of the run: handed on to that one process
        if run.poll() is None:
            run.send_signal(signum)
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, hand_on)
    kept = bytearray()
    while True:
        chunk = run.stdout.read1(65536)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        kept += chunk
    code = run.wait()
    try:
        with open(res, encoding="utf-8") as f:
            result = json.load(f)
    except OSError:
        result = None
    except ValueError:
        result = "not json"
    try:
        os.remove(res)
    except OSError:
        pass
    why = verdict(code, kept.decode("utf-8", "replace"), result, count)
    if kept and not kept.endswith(b"\n"):
        print()                 # a run cut in the middle of a line: this file's own line starts at the margin
    if why:
        print("run_whole: NOT GREEN: " + "; ".join(why) + ".")
        return 1
    print(f"run_whole: GREEN: {count} tests defined and {count} run, exit 0, the summary, the gate's line and the "
          "run's own result are there.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
