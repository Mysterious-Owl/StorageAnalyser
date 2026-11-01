"""
Checks the right-click menu command lines exactly as Explorer sends them
(raw command strings, so Windows argv quoting quirks are reproduced), plus
the web server's request guards.

Usage: python tests/test_context_menu.py <scratch_dir>
Everything is written under <scratch_dir>; a free drive letter is mapped to a
tiny synthetic folder with `subst` for the drive-root case and removed after.
"""
import json
import os
import re
import shutil
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
scratch = os.path.abspath(sys.argv[1])
data = os.path.join(scratch, "ctx data; live")     # space AND ';' in the name
out_dir = os.path.join(scratch, "out")
shutil.rmtree(scratch, ignore_errors=True)
for p in [r"a\x\f.txt", r"b\x\f.txt", r"c\g.txt"]:
    fp = os.path.join(data, p)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w") as fh:
        fh.write("hello" * 300)                       # 1.5 KB, above the 1 KB default
env = dict(os.environ, PYTHONIOENCODING="utf-8", DD_OUT_DIR=out_dir)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def inspect(arg):
    cmd = f'"{PY}" "{ROOT}\\tools\\inspect_folder.py" --depth 2 --pause "{arg}"'
    return subprocess.run(cmd, capture_output=True, text=True, env=env, input="\n")


# 1) folder with trailing backslash, spaces and ';' (python.exe directly, as the menu does)
r = inspect(data + "\\")
check("quick summary: path with '; ' and trailing \\",
      "Total 4.4 KB, 3 files" in r.stdout and data in r.stdout, f"rc={r.returncode}")
check("quick summary: waits for Enter", "Press Enter to close" in r.stdout)

# 2) drive root exactly as Explorer passes it: "X:\" (argv sees X:")
free = next(l for l in reversed(string.ascii_uppercase) if not os.path.exists(f"{l}:\\"))
subprocess.run(["subst", f"{free}:", data], check=True)
try:
    r = inspect(f"{free}:\\")
    first = r.stdout.splitlines()[1] if len(r.stdout.splitlines()) > 1 else ""
    check(f"quick summary: drive root {free}:\\", first.strip() == f"{free}:\\" and "3 files" in r.stdout)
    # 3) app opened from the menu on a drive root
    cmd = f'"{PY}" "{ROOT}\\app\\dd_web.py" 8797 --no-browser --page dups --scan "{free}:\\"'
    proc = subprocess.Popen(cmd, env=env)
    base = "http://127.0.0.1:8797"
    for _ in range(50):
        try:
            html = urllib.request.urlopen(base + "/").read().decode()
            break
        except OSError:
            time.sleep(0.2)
    token = re.search(r'name="token" content="([^"]+)"', html).group(1)

    def call(path, body=None, host=None):
        h = {"X-Token": token, "Content-Type": "application/json"}
        if host:
            h["Host"] = host
        req = urllib.request.Request(base + path, headers=h,
                                     data=None if body is None else json.dumps(body).encode())
        return json.loads(urllib.request.urlopen(req).read())

    while True:
        s = call("/api/status")
        if not s["job"]["busy"] and s["scan"] and s["dups"]:
            break
        time.sleep(0.2)
    check("app --scan drive root", s["scan"]["roots"][0]["path"] == f"{free}:\\",
          f"files={s['scan']['files']} groups={s['dups']['groups']}")
    check("app right-click scan not cached", not os.path.exists(os.path.join(out_dir, "last_scan.pkl")))

    # 4) request guards
    try:
        call("/api/status", host="evil.example:8797")
        check("rejects foreign Host (DNS rebinding)", False)
    except urllib.error.HTTPError as e:
        check("rejects foreign Host (DNS rebinding)", e.code == 403)
    try:
        urllib.request.urlopen(urllib.request.Request(base + "/", headers={"Host": "evil.example:8797"}))
        check("index.html not served to foreign Host", False)
    except urllib.error.HTTPError as e:
        check("index.html not served to foreign Host", e.code == 403)
    proc.terminate()
finally:
    subprocess.run(["subst", f"{free}:", "/d"])

# 5) job guard, deterministically (in-process: mark a job busy, then try to start others)
sys.path.insert(0, os.path.join(ROOT, "app"))
os.environ["DD_OUT_DIR"] = out_dir     # never touch the real 'app output'
import dd_web  # noqa: E402

assert dd_web.OUT_DIR == out_dir

dd_web.S.job["busy"] = True
before = dd_web.S.scanner
r = dd_web.start_scan({"folders": [data]})
check("busy: scan refused with an error", r.get("ok") is False and "error" in r)
check("busy: running scanner not replaced", dd_web.S.scanner is before)
dd_web.S.res = object()
r = dd_web.start_export({"keys": ["folders"]})
check("busy: export refused with an error", r.get("ok") is False and "error" in r)
dd_web.S.job["busy"] = False
dd_web.S.res = None

# a cancelled scan must not overwrite the saved scan (CACHE lives in DD_OUT_DIR)
class CancelledScanner(dd_web.sc.Scanner):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.cancel = True


orig, dd_web.sc.Scanner = dd_web.sc.Scanner, CancelledScanner
dd_web.start_scan({"folders": [data]})
while dd_web.S.job["busy"]:
    time.sleep(0.05)
dd_web.sc.Scanner = orig
check("cancelled scan is not saved", dd_web.S.res.cancelled and not os.path.exists(dd_web.CACHE),
      f"cancelled={dd_web.S.res.cancelled}")

# 6) normalize_roots: bare "Q:" means the drive root, not Q:'s current directory
import dd_scanner  # noqa: E402

check("normalize_roots('X:') -> 'X:\\'", dd_scanner.normalize_roots(["X:"]) == ["X:\\"],
      str(dd_scanner.normalize_roots(["X:"])))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
