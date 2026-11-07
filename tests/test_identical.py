"""
Byte-identical finder: engine + quarantine/undo + web API, on synthetic files.

Usage: python tests/test_identical.py <scratch_dir>
A free drive letter is mapped to the scratch folder with `subst`, so the
quarantine folder (created at the drive root) lands inside scratch. Nothing
outside <scratch_dir> is touched (DD_OUT_DIR points there too).
"""
import json
import os
import re
import shutil
import string
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
scratch = os.path.abspath(sys.argv[1])
shutil.rmtree(scratch, ignore_errors=True)
os.makedirs(scratch)
os.environ["DD_OUT_DIR"] = os.path.join(scratch, "_out")
sys.path.insert(0, os.path.join(ROOT, "app"))
import dd_identical as idf  # noqa: E402
import dd_scanner as sc  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


free = next(l for l in reversed(string.ascii_uppercase) if not os.path.exists(f"{l}:\\"))
subprocess.run(["subst", f"{free}:", scratch], check=True)
D = f"{free}:\\data"
try:
    def write(rel, data, mtime=None):
        p = os.path.join(D, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
        if mtime:
            os.utime(p, (mtime, mtime))
        return p

    big1 = os.urandom(300_000)
    mid = bytearray(big1)
    mid[150_000:150_010] = b"XXXXXXXXXX"            # same head/tail, different middle
    now = time.time()
    write(r"live\photo.jpg", big1, now - 3000)
    write(r"backup\2023\photo.jpg", big1, now - 9000)        # oldest
    write(r"phone\IMG_0001.jpg", big1, now - 100)            # newest, different name
    write(r"other\photo.jpg", bytes(mid), now - 50)          # passes quick check only
    write(r"other\random.bin", os.urandom(300_000))          # same size, different head
    write(r"docs\a.txt", b"hello" * 300)
    write(r"docs\copy\a.txt", b"hello" * 300)
    write(r"docs\b.txt", b"HELLO" * 300)                     # same size, different
    write(r"empty1.txt", b"")
    write(r"empty2.txt", b"")
    os.makedirs(os.path.join(D, "links"))
    os.link(os.path.join(D, r"docs\a.txt"), os.path.join(D, r"links\a_link.txt"))

    res = sc.Scanner().scan([D])
    cache = os.path.join(os.environ["DD_OUT_DIR"], "hash_cache.pkl")
    os.makedirs(os.environ["DD_OUT_DIR"], exist_ok=True)
    r1 = idf.find_identical(res, min_size=1, cache_path=cache)
    by = {g["size"]: g for g in r1.groups}
    names = lambda g: sorted(os.path.relpath(res.file_path(f), D) for f in g["files"])  # noqa: E731
    check("2 identical groups", len(r1.groups) == 2, [names(g) for g in r1.groups])
    check("big group = 3 true copies (not the middle-changed one)",
          names(by[300_000]) == ["backup\\2023\\photo.jpg", "live\\photo.jpg", "phone\\IMG_0001.jpg"])
    check("small group incl. hard link", names(by[1500]) == ["docs\\a.txt", "docs\\copy\\a.txt", "links\\a_link.txt"])
    check("hard link frees nothing", by[1500]["hardlinked"] and by[1500]["reclaimable"] == 1500,
          f"reclaimable={by[1500]['reclaimable']}")
    check("reclaimable big = 2 copies", by[300_000]["reclaimable"] == 600_000)
    check("empty files ignored", all(g["size"] > 0 for g in r1.groups))
    check("quick check filtered, full hash caught middle change",
          r1.stats["afterQuickCheck"] == 4 + 3 and r1.stats["fullyHashed"] == 4, r1.stats)

    r2 = idf.find_identical(res, min_size=1, cache_path=cache)
    check("re-run served from cache", r2.stats["fullyHashed"] == 0 and r2.stats["cacheHits"] > 0,
          f"hits={r2.stats['cacheHits']}")
    r3 = idf.find_identical(res, min_size=1, verify=True, cache_path=cache)
    check("byte-by-byte verify agrees", len(r3.groups) == 2 and r3.stats.get("hashCollisions", 0) == 0)
    r4 = idf.find_identical(res, min_size=1, scope=os.path.join(D, "docs"), cache_path=cache)
    check("scope limits to folder", len(r4.groups) == 1 and r4.groups[0]["size"] == 1500)

    # extension filters (identical + same-name)
    import dd_duplicates as dup
    r5 = idf.find_identical(res, min_size=1, include_ext=[".JPG"], cache_path=cache)
    check("ext: only .jpg", [g["size"] for g in r5.groups] == [300_000] and r5.stats["skippedExt"] > 0)
    r6 = idf.find_identical(res, min_size=1, exclude_ext=["txt"], cache_path=cache)
    check("ext: all except txt", [g["size"] for g in r6.groups] == [300_000])
    r7 = idf.find_identical(res, min_size=1, include_ext=["jpg", "txt"], exclude_ext=["txt"], cache_path=cache)
    check("ext: exclude wins over include", [g["size"] for g in r7.groups] == [300_000])
    d1 = dup.build(res, 0, include_ext=["txt"])
    check("same-name: only .txt", {g["name"].lower() for g in d1.groups} == {"a.txt"} and d1.ext_skipped > 0,
          [g["name"] for g in d1.groups])
    d2 = dup.build(res, 0, exclude_ext=["txt"])
    check("same-name: except .txt", "a.txt" not in {g["name"].lower() for g in d2.groups})
    check("ext: '(none)' means no extension", dup.make_ext_filter(["(none)"])("README") and
          not dup.make_ext_filter(["(none)"])("a.txt"))
    stats = dict((e, (n, b)) for e, n, b in dup.extension_stats(res))
    check("extension stats", stats["jpg"] == (4, 1_200_000) and stats["txt"][0] == 6, stats.get("jpg"))
    check("everything treated as local (no cloud option)",
          "skip_cloud" not in idf.find_identical.__code__.co_varnames)

    g = by[300_000]
    kp = lambda rule, text="": os.path.relpath(res.file_path(idf.choose_keeper(res, g, rule, text)), D)  # noqa: E731
    check("keeper newest", kp("newest") == "phone\\IMG_0001.jpg")
    check("keeper oldest", kp("oldest") == "backup\\2023\\photo.jpg")
    check("keeper shortest", kp("shortest") == "live\\photo.jpg")
    check("keeper avoid 'backup'", kp("avoid", "backup") != "backup\\2023\\photo.jpg")
    check("keeper prefer 'backup'", kp("prefer", "Backup") == "backup\\2023\\photo.jpg")

    # quarantine: keep 'live', move the other two big copies + the non-link small copy
    keep_big = next(f for f in g["files"] if "live" in res.file_path(f))
    keep_small = next(f for f in by[1500]["files"] if res.file_path(f).endswith(r"docs\a.txt"))
    moves = idf.plan_moves(res, r1, [(g, keep_big), (by[1500], keep_small)])
    check("plan skips hard link of keeper", len(moves) == 3, [os.path.relpath(m["src"], D) for m in moves])
    qlogs = os.path.join(os.environ["DD_OUT_DIR"], "quarantine")
    run_id, log, done = idf.quarantine(moves, qlogs)
    qdir = os.path.join(f"{free}:\\", idf.QUARANTINE_DIR, run_id)
    moved_ok = all(not os.path.exists(m["src"]) and
                   os.path.exists(os.path.join(qdir, m["src"][3:])) for m in moves)
    check("files moved into quarantine (same drive, path kept)", moved_ok and len(done) == 3)
    check("kept copy untouched", os.path.exists(res.file_path(keep_big)))
    runs = idf.list_runs(qlogs)
    check("run listed", runs and runs[0]["moved"] == 3 and runs[0]["bytes"] == 601_500, runs)
    restored, total = idf.undo(qlogs, run_id)
    check("undo restores everything", restored == 3 and all(os.path.exists(m["src"]) for m in moves))
    check("run marked undone", idf.list_runs(qlogs)[0]["undone"])

    # changed-since-scan safety
    p = res.file_path(next(f for f in g["files"] if "phone" in res.file_path(f)))
    with open(p, "ab") as fh:
        fh.write(b"!")
    _, log2, done2 = idf.quarantine(idf.plan_moves(res, r1, [(g, keep_big)]), qlogs)
    check("file changed since scan is skipped", os.path.exists(p) and len(done2) == 1)
    idf.undo(qlogs, os.path.basename(log2)[:-4])

    # ---- web API end-to-end
    env = dict(os.environ)
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "dd_web.py"), "8796", "--no-browser",
                             "--scan", D], env=env)
    base = "http://127.0.0.1:8796"
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

    def wait():
        while True:
            s = call("/api/status")
            if not s["job"]["busy"]:
                return s
            time.sleep(0.1)

    wait()
    ex_api = call("/api/extensions")["exts"]
    check("api: extensions listed", {"jpg", "txt", "bin"} <= {x["ext"] for x in ex_api}, ex_api[:3])
    call("/api/identical", {"minKb": 0, "includeExt": ["txt"]})
    s = wait()
    check("api: identical with only .txt", s["identical"]["groups"] == 1 and s["identical"]["skippedExt"] > 0)
    call("/api/identical", {"minKb": 0})
    s = wait()
    check("api: identical summary", s["identical"] and s["identical"]["openGroups"] == 2, s["identical"])
    gl = call("/api/idgroups?sort=size")
    check("api: groups listed", gl["total"] == 2 and [r["size"] for r in gl["rows"]] == [300_000, 1500],
          [(r["size"], r["copies"]) for r in gl["rows"]])
    big = next(r for r in gl["rows"] if r["copies"] >= 2 and r["size"] >= 300_000)
    gd = call(f"/api/idgroup?gid={big['gid']}&rule=avoid&text=backup")
    keep = next(c for c in gd["copiesList"] if c["keep"])
    check("api: keeper suggested by rule", "backup" not in keep["path"].lower(), keep["path"])
    dry = call("/api/quarantine", {"mode": "group", "gid": big["gid"], "keep": keep["id"], "dryRun": True})
    check("api: dry run counts", dry["ok"] and dry["files"] == big["copies"] - 1, dry)
    call("/api/quarantine", {"mode": "group", "gid": big["gid"], "keep": keep["id"]})
    s = wait()
    check("api: moved", s["identical"]["moved"] == big["copies"] - 1 and s["job"]["msg"].startswith("Moved"),
          s["job"]["msg"])
    runs = call("/api/qruns")["runs"]
    call("/api/undo", {"run": runs[0]["run"]})
    s = wait()
    check("api: undo", s["identical"]["moved"] == 0 and call("/api/qruns")["runs"][0]["undone"]
          and s["job"]["msg"].startswith("Restored"), s["job"]["msg"])
    check("run ids unique", len({r["run"] for r in call("/api/qruns")["runs"]}) == len(call("/api/qruns")["runs"]))
    call("/api/export", {"keys": ["identical"]})
    wait()
    check("api: export identical_files.csv",
          os.path.exists(os.path.join(os.environ["DD_OUT_DIR"], "identical_files.csv")))
    proc.terminate()
finally:
    subprocess.run(["subst", f"{free}:", "/d"])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
