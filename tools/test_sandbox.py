#!/usr/bin/env python3
"""Adversarial sandbox test — run ON kepler (needs bwrap). Verifies the hardened
sandbox blocks env leakage / fork bombs / host-file reads while running real code."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from game import sandbox_runner

# Put a secret in the server env to prove --clearenv hides it.
os.environ["OCRCADE_RTB_ADMIN_PASSWORD"] = "SUPER_SECRET_SHOULD_NOT_LEAK"


def show(name, res, expect):
    out = (res["stdout"] + res["stderr"]).strip()
    leaked = "SUPER_SECRET_SHOULD_NOT_LEAK" in out
    print(f"\n=== {name} (expect: {expect}) ===")
    print(f"  timed_out={res['timed_out']} exit={res['exit_code']} secret_leaked={leaked}")
    print("  out:", out[:300].replace("\n", " | "))


# 1. normal python — must work
show("normal python", sandbox_runner.run(1, "python",
     "with open('/puzzle/flag1.txt') as f: print('read puzzle ok')\nprint(2+2)"), "works, 4")

# 2. env leak attempt — secret must NOT appear
show("env leak", sandbox_runner.run(1, "python",
     "import os; print(dict(os.environ))"), "no secret, clean env")

# 3. read host file — must fail (no host fs)
show("host file read", sandbox_runner.run(1, "python",
     "print(open('/home/sakebomb/ocrcade-v2-test/teams.json').read())"), "FileNotFoundError")

# 4. fork bomb — must be capped by ulimit -u, not hang the host
show("fork bomb", sandbox_runner.run(1, "bash",
     ":(){ :|:& };:"), "contained / blocked")

# 5. C++ #include host file — compile must fail to read it
show("cpp include host", sandbox_runner.run(1, "cpp",
     '#include "/etc/shadow"\nint main(){return 0;}'), "compile error, no shadow contents")

# 6. C++ normal — must compile+run in sandbox
show("cpp normal", sandbox_runner.run(1, "cpp",
     '#include <cstdio>\nint main(){printf("cpp ok %d\\n", 7);return 0;}'), "cpp ok 7")
