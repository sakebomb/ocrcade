#!/usr/bin/env python3
"""Adversarial sandbox test — run ON kepler (needs bwrap). Verifies the hardened
sandbox blocks env leakage / fork bombs / host-file reads while running real code,
caps a whole run's memory at 1 GB, and runs one submission at a time.

The memory cap needs a delegated cgroup, as ocrcade-exec.service has. Run it in one:
    sudo systemd-run --uid=sakebomb -p Delegate=yes -E OCRCADE_SANDBOX_CGROUP=1 \\
        --wait --pipe --quiet --same-dir \\
        python3 tools/test_sandbox.py
"""
import os
import sys
import threading

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

print(f"\n--- cgroup: {sandbox_runner.run_cgroup().status}")

# 7. memory bomb across processes: 4 x 400 MB, each under the per-process ulimit,
#    1.6 GB together — must be killed by the 1 GB run cap, and the host keeps running
MEM_BOMB = """import os, time
for _ in range(2):
    os.fork()
blob = b'x' * (400 * 1024 * 1024)
time.sleep(4)
print('bomb survived', os.getpid())
"""
r = sandbox_runner.run(1, "python", MEM_BOMB)
show("multi-process memory bomb", r, "oom=True, no 'bomb survived'")
print(f"  oom={r['oom']}")

# 8. one process using 300 MB — must still work under the cap
r = sandbox_runner.run(1, "python", "b = b'x' * (300 * 1024 * 1024)\nprint('300MB ok', len(b))")
show("300 MB in one process", r, "300MB ok")

# 9. two submissions at once: one runs, the other is told the booth is busy
results = []
slow = f"import time; time.sleep({sandbox_runner.SLOT_WAIT + 2}); print('slow done')"
threads = [threading.Thread(target=lambda: results.append(sandbox_runner.run(1, "python", slow)))
           for _ in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("\n=== two at once (expect: one 'slow done', one busy) ===")
print("  ", [("busy" if x["busy"] else x["stdout"].strip()) for x in results])
