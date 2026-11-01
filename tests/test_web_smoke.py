"""
Smoke test of the web UI backend against a synthetic folder tree.
Usage: python tests/test_web_smoke.py <scratch_dir> [--keep]
Outputs go to <scratch_dir>/out (DD_OUT_DIR), never to 'app output'.
With --keep the server stays up on port 8799 (for screenshots) until it
idles out; otherwise it is stopped at the end.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
scratch = sys.argv[1]
data = os.path.join(scratch, "data")
out = os.path.join(scratch, "out")
shutil.rmtree(scratch, ignore_errors=True)
for p in [r"live\Projects\A\report.docx", r"live\Projects\A\data.csv", r"live\Photos\2024\img1.jpg",
          r"backup\2023\Projects\A\report.docx", r"backup\2023\Projects\A\data.csv",
          r"backup\2023\Photos\2024\img1.jpg", r"backup\2023\Projects\B\report.docx",
          r"other\notes\report.docx", r"live\node_modules\x\index.js", r"Downloads\setup.exe"]:
    fp = os.path.join(data, p)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "wb") as fh:
        fh.write(b"x" * (50000 if p.endswith(".jpg") else 300000 if p.endswith(".exe") else 4000))

env = dict(os.environ, DD_OUT_DIR=out)
proc = subprocess.Popen([sys.executable, os.path.join(APP, "dd_web.py"), "8799", "--no-browser"],
                        env=env, cwd=APP,
                        creationflags=0x00000008 if "--keep" in sys.argv else 0)  # DETACHED_PROCESS
base = "http://127.0.0.1:8799"
for _ in range(50):
    try:
        html = urllib.request.urlopen(base + "/").read().decode()
        break
    except OSError:
        time.sleep(0.2)
token = re.search(r'name="token" content="([^"]+)"', html).group(1)


def call(path, body=None):
    req = urllib.request.Request(base + path, headers={"X-Token": token, "Content-Type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    return json.loads(urllib.request.urlopen(req).read())


# no token -> forbidden
try:
    urllib.request.urlopen(base + "/api/status")
    print("FAIL: no-token request allowed")
except urllib.error.HTTPError as e:
    print("no-token request ->", e.code)

print("init drives:", [d["root"] for d in call("/api/init")["drives"]])
print("scan:", call("/api/scan", {"drives": [], "userFolders": False, "folders": [data],
                                  "dupOptions": {"minKb": 0, "skipDev": True, "skipSystem": True}})["ok"])
while True:
    s = call("/api/status")
    if not s["job"]["busy"]:
        break
    time.sleep(0.2)
print("job:", s["job"], "| files", s["scan"]["files"], "| groups", s["dups"]["groups"])
root = s["scan"]["roots"][0]["id"]
f = call(f"/api/folder?id={root}")
print("folder children:", [(c["name"], c["size"]) for c in f["children"]])
print("levels:", [(l["level"], l["folders"]) for l in call(f"/api/levels?root={root}")["levels"]])
print("level 1 rows:", len(call("/api/level?level=1&root=all")["rows"]))
g = call("/api/groups?sort=copies")
print("groups:", [(r["name"], r["copies"], r["maxLevels"]) for r in g["rows"]])
grp = call(f"/api/group?gid={g['rows'][0]['gid']}")
print("copies:", [(c["levels"], c["rel"]) for c in grp["copiesList"]])
print("groups path filter:", call("/api/groups?path=" + urllib.parse.quote(os.path.join(data, "other")))["total"])
print("pairs:", [(p["files"], p["levels"]) for p in call("/api/pairs?min=1")["rows"]])
print("open root outside scan rejected silently:", call("/api/open", {"kind": "root", "path": "C:\\Windows"}))
call("/api/export", {"keys": ["folders", "levels", "dups", "errors"]})
while call("/api/status")["job"]["busy"]:
    time.sleep(0.2)
print("exports:", sorted(os.listdir(out)))
if "--keep" in sys.argv:
    print("SERVER_PID", proc.pid)
else:
    proc.terminate()
    print("server stopped")
