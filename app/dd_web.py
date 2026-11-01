"""
Data Dedup & Storage Explorer - modern web UI (local only).

Starts a small HTTP server on 127.0.0.1 (never reachable from the network),
serves the UI from ./web and opens it in an Edge app window (or the default
browser). Nothing is sent to the internet: the page loads no external
resources and the server only talks to this PC.

Security model (the server holds the user's whole file listing):
  * binds 127.0.0.1 only - not reachable from the network;
  * every request must carry Host 127.0.0.1:<port> / localhost:<port>, which
    defeats DNS-rebinding pages that resolve their own domain to 127.0.0.1;
  * every API call needs a per-run random token that is only embedded in the
    page this server serves (other origins can't read it - same-origin policy);
  * "Open in Explorer" only accepts folder / file ids from the current scan
    (or roots inside it), never arbitrary paths;
  * strict Content-Security-Policy: the page loads nothing from elsewhere.

Run:  python app/dd_web.py    (or double-click "Storage Explorer.bat")
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import dd_duplicates as dup
import dd_export as ex
import dd_identical as idf
import dd_scanner as sc
from dd_folderinfo import FolderInfo

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
OUT_DIR = os.environ.get("DD_OUT_DIR") or os.path.join(os.path.dirname(HERE), "app output")
CACHE = os.path.join(OUT_DIR, "last_scan.pkl")
HASH_CACHE = os.path.join(OUT_DIR, "hash_cache.pkl")
QUARANTINE_LOGS = os.path.join(OUT_DIR, "quarantine")
TOKEN = secrets.token_urlsafe(24)
ALLOWED_HOSTS = set()   # filled in main(): "127.0.0.1:<port>", "localhost:<port>"
IDLE_EXIT = 90          # seconds without a page heartbeat -> server exits
# Worker processes for big scans. Only switched on by main(): Windows starts
# workers by re-importing the entry script, which is safe for dd_web.py (its
# server only starts under __main__) but not for arbitrary importers (tests).
USE_PROCESSES = False


class State:
    def __init__(self):
        self.fi = FolderInfo()
        self.res = None
        self.dr = None
        self.scanner = None
        self.idr = None          # identical-files result (dd_identical.IdResult)
        self.idctl = None        # progress/cancel of the identical / quarantine job
        self.run_ids = {}        # quarantine run id -> file ids moved (for undo)
        self.root_of = []
        self.desc_cache = {}
        self.job = {"busy": False, "kind": "", "msg": "", "error": ""}
        self.lock = threading.Lock()
        self.last_ping = time.time()
        self.export_log = []


S = State()


# ------------------------------------------------------------------ helpers
def abs_level(p):
    return ex.abs_level(p)


def describe(i):
    """(title, desc, category, advice, inherited) for folder i."""
    hit = S.desc_cache.get(i)
    if hit is not None:
        return hit
    r = S.res
    t, d, c, a = S.fi.describe(r.d_path[i])
    if t:
        hit = (t, d, c, a, False)
    else:
        p = r.d_parent[i]
        hit = ("", "", "", "", False)
        if p >= 0:
            pt = describe(p)
            if pt[0]:
                hit = (pt[0], pt[1], pt[2], pt[3], True)
    S.desc_cache[i] = hit
    return hit


def node(i, parent_size=None):
    r = S.res
    path = r.d_path[i]
    t, d, c, a, inh = describe(i)
    ps = parent_size if parent_size else r.d_size[i]
    return {
        "id": i, "name": os.path.basename(path.rstrip("\\")) or path, "path": path,
        "size": r.d_size[i], "files": r.d_files[i], "dirs": r.d_dirs[i],
        "cloud": r.d_cloud[i], "latest": r.d_latest[i], "level": abs_level(path),
        "pct": (r.d_size[i] / ps) if ps else 0,
        "title": t, "desc": d, "cat": c, "advice": a, "inherited": inh,
        "hasChildren": bool(r.children(i)),
    }


def drive_info(path):
    info = getattr(S.res, "drive_info", {}).get(path)
    if not info:
        return None
    return {"root": info[0], "type": info[1], "label": info[2],
            "total": info[3], "used": info[4], "free": info[5]}


def scan_summary():
    r = S.res
    if r is None:
        return None
    return {
        "files": len(r.f_name), "folders": len(r.d_path), "errors": len(r.errors),
        "links": r.skipped_links, "cancelled": r.cancelled,
        "seconds": round((r.finished - r.started) if r.finished else 0),
        "finished": r.finished,
        "size": sum(r.d_size[i] for _, i in r.roots),
        "roots": [{**node(i), "drive": drive_info(p)} for p, i in r.roots],
    }


def dup_summary():
    dr = S.dr
    if dr is None:
        return None
    return {
        "groups": len(dr.groups),
        "files": sum(g["copies"] for g in dr.groups),
        "sameSize": sum(g["same_size"] for g in dr.groups),
        "reclaimable": sum(g["reclaimable"] for g in dr.groups),
        "multiDrive": sum(g["multi_drive"] for g in dr.groups),
        "excluded": dr.excluded_files, "extSkipped": dr.ext_skipped, "pairs": len(dr.pairs),
        "levels": dr.level_summary,
    }


def id_summary():
    idr = S.idr
    if idr is None:
        return None
    left = [_remaining(g) for g in idr.groups]
    return {**idr.stats, "moved": len(idr.resolved),
            "openGroups": sum(len(r) > 1 for r in left),
            "openReclaimable": sum(_reclaimable(g, r) for g, r in zip(idr.groups, left))}


def _remaining(g):
    return [f for f in g["files"] if f not in S.idr.resolved]


def _reclaimable(g, files):
    """Space freed by keeping one copy of `files` (hard links count once)."""
    return g["size"] * max(0, len({g["linkid"][f] for f in files}) - 1)


def set_res(res):
    S.res = res
    S.dr = None
    S.idr = None
    S.desc_cache = {}
    root_of = [0] * len(res.d_path)
    for i in range(len(res.d_path)):
        p = res.d_parent[i]
        root_of[i] = i if p < 0 else root_of[p]
    S.root_of = root_of


# ------------------------------------------------------------------ jobs
BUSY = {"ok": False, "error": "Another task is still running - wait for it to finish."}


def run_job(kind, fn, on_start=None):
    """Start fn in a worker thread unless a job is already running.
    on_start runs under the lock only when the job is accepted.
    Returns an API response dict ({"ok": True} or BUSY)."""
    with S.lock:
        if S.job["busy"]:
            return BUSY
        if on_start:
            on_start()
        S.job = {"busy": True, "kind": kind, "msg": "Starting...", "error": ""}

    def wrap():
        try:
            fn()
            S.job["msg"] = S.job.pop("result", "") or "Done"
        except Exception as e:  # noqa: BLE001 - report to UI
            traceback.print_exc()
            S.job["error"] = f"{type(e).__name__}: {e}"
        finally:
            S.job["busy"] = False

    threading.Thread(target=wrap, daemon=True).start()
    return {"ok": True}


def job_dups(opts):
    def prog(i, n):
        S.job["msg"] = f"Matching names: {i:,} / {n:,} groups"
    S.job["kind"] = "dups"
    S.job["msg"] = "Grouping files by name..."
    S.dr = dup.build(S.res, int(float(opts.get("minKb", 1)) * 1024),
                     bool(opts.get("skipDev", True)), bool(opts.get("skipSystem", True)), prog,
                     include_ext=opts.get("includeExt"), exclude_ext=opts.get("excludeExt"))


def start_scan(body):
    roots = list(body.get("drives", []))
    if body.get("userFolders"):
        roots += sc.user_folders()
    roots += [p for p in body.get("folders", []) if p]
    roots = sc.normalize_roots(roots)
    if not roots:
        return {"ok": False, "error": "Select at least one drive or folder."}
    scanner = sc.Scanner(use_processes=USE_PROCESSES)

    def fn():
        res = scanner.scan(roots)
        res.drive_info = {d[0]: d for d in sc.list_drives()}
        set_res(res)
        # save the scan in the background while same-name matching runs;
        # a cancelled (partial) scan must not replace a complete saved one
        saver = None
        if body.get("saveCache", True) and not res.cancelled:
            def save():
                try:
                    os.makedirs(OUT_DIR, exist_ok=True)
                    res.save(CACHE)
                except Exception as e:  # noqa: BLE001 - cache optional
                    res.errors.append((CACHE, f"cache not saved: {e}"))
            saver = threading.Thread(target=save, daemon=True)
            saver.start()
        job_dups(body.get("dupOptions", {}))
        if saver:
            S.job["msg"] = "Saving scan for next time..."
            saver.join()

    # only swap the progress-reporting scanner once the job is accepted
    r = run_job("scan", fn, on_start=lambda: setattr(S, "scanner", scanner))
    return {**r, "roots": roots}


def start_load(body):
    if not os.path.exists(CACHE):
        return {"ok": False, "error": "No saved scan yet."}

    def fn():
        S.job["msg"] = "Loading saved scan..."
        set_res(sc.ScanResult.load(CACHE))
        job_dups(body.get("dupOptions", {}))

    return run_job("load", fn)


def start_dups(body):
    if S.res is None:
        return {"ok": False, "error": "Run a scan first."}
    return run_job("dups", lambda: job_dups(body))


def start_export(body):
    if S.res is None:
        return {"ok": False, "error": "Run a scan first."}
    keys = body.get("keys", [])
    if "dups" in keys and S.dr is None:
        return {"ok": False, "error": "Same-name matching has not finished yet."}
    if "identical" in keys and S.idr is None:
        return {"ok": False, "error": "Run 'Find identical files' first."}

    writers = {
        "folders": lambda: [ex.folder_sizes(S.res, S.fi, OUT_DIR)],
        "levels": lambda: [ex.level_summary(S.res, OUT_DIR)],
        "dups": lambda: ex.duplicates(S.res, S.dr, S.fi, OUT_DIR),
        "inventory": lambda: [ex.inventory(S.res, OUT_DIR)],
        "errors": lambda: [ex.errors(S.res, OUT_DIR)],
        "identical": lambda: [ex.identical(S.res, S.idr, OUT_DIR)],
    }

    def fn():
        os.makedirs(OUT_DIR, exist_ok=True)
        S.job["msg"] = f"Exporting {', '.join(keys)}..."
        # each CSV is independent -> write them concurrently
        with ThreadPoolExecutor(max_workers=max(1, len(keys))) as pool:
            made = [p for paths in pool.map(lambda k: writers[k](), [k for k in keys if k in writers])
                    for p in paths]
        for m in made:
            S.export_log.append({"file": os.path.basename(m), "size": os.path.getsize(m),
                                 "time": time.time()})

    return run_job("export", fn)


# ---- identical files (reads contents locally to hash them)
def start_identical(body):
    if S.res is None:
        return {"ok": False, "error": "Run a scan first."}
    ctl = idf.Control()

    def fn():
        os.makedirs(OUT_DIR, exist_ok=True)
        S.job["msg"] = "Finding identical files..."
        try:
            S.idr = idf.find_identical(
                S.res, min_size=int(float(body.get("minKb", 1)) * 1024),
                skip_dev=bool(body.get("skipDev", True)), skip_system=bool(body.get("skipSystem", True)),
                skip_cloud=False,                      # everything is treated as local
                verify=bool(body.get("verify", False)),
                scope=body.get("path", "").strip(), ctl=ctl, cache_path=HASH_CACHE,
                include_ext=body.get("includeExt"), exclude_ext=body.get("excludeExt"))
        except idf.Cancelled:
            S.job["result"] = "Cancelled"

    return run_job("identical", fn, on_start=lambda: setattr(S, "idctl", ctl))


def _id_filtered(q):
    """Identical groups matching the filter query (dict of lists, like parse_qs)."""
    r = S.res
    g1 = lambda k, d="": (q.get(k, [d]) or [d])[0]  # noqa: E731
    name, path = g1("name").lower().strip(), g1("path").lower().strip()
    drives = g1("drives", "all")
    min_bytes = float(g1("minMb", "0") or 0) * 1048576
    out = []
    for g in S.idr.groups:
        files = _remaining(g)
        if len(files) < 2 or g["size"] < min_bytes:
            continue
        if name and not any(name in n.lower() for n in g["names"]):
            continue
        if drives == "multi" and not g["multi_drive"]:
            continue
        if drives == "single" and g["multi_drive"]:
            continue
        if path and not any(path in r.file_path(f).lower() for f in files):
            continue
        out.append((g, files))
    return out


ID_SORTS = {"reclaimable": lambda gf: -_reclaimable(*gf), "size": lambda gf: -gf[0]["size"],
            "copies": lambda gf: -len(gf[1]), "name": lambda gf: gf[0]["names"][0].lower(),
            "newest": lambda gf: -max(S.res.f_mtime[f] for f in gf[1])}


def q_idgroups(q):
    if S.idr is None:
        return {"total": 0, "rows": []}
    r = S.res
    rows = _id_filtered(q)
    rows.sort(key=ID_SORTS.get(q.get("sort", ["reclaimable"])[0], ID_SORTS["reclaimable"]))
    offset, limit = int(q.get("offset", ["0"])[0]), int(q.get("limit", ["200"])[0])
    page = []
    for g, files in rows[offset:offset + limit]:
        folders = sorted({r.d_path[r.f_dir[f]] for f in files}, key=str.lower)
        page.append({"gid": g["gid"], "name": g["names"][0], "otherNames": len(g["names"]) - 1,
                     "size": g["size"], "copies": len(files), "reclaimable": _reclaimable(g, files),
                     "hardlinked": g["hardlinked"], "drives": g["drives"],
                     "folders": folders[:2], "moreFolders": max(0, len(folders) - 2),
                     "newest": max(r.f_mtime[f] for f in files)})
    return {"total": len(rows), "reclaimable": sum(_reclaimable(g, f) for g, f in rows),
            "rows": page}


def q_idgroup(q):
    r = S.res
    g = S.idr.groups[int(q["gid"][0]) - 1]
    files = _remaining(g)
    keep = idf.choose_keeper(r, {**g, "files": files}, q.get("rule", ["newest"])[0],
                             q.get("text", [""])[0]) if len(files) > 1 else None
    seen, copies = {}, []
    for f in g["files"]:
        link = g["linkid"][f]
        copies.append({"id": f, "folderId": r.f_dir[f], "path": r.file_path(f), "size": r.f_size[f],
                       "created": r.f_ctime[f], "modified": r.f_mtime[f],
                       "newest": f == g["newest"], "moved": f in S.idr.resolved,
                       "linkOf": seen.get(link), "keep": f == keep})
        seen.setdefault(link, r.file_path(f))
    return {"gid": g["gid"], "hash": g["hash"], "size": g["size"], "names": g["names"],
            "reclaimable": _reclaimable(g, files), "copiesList": copies}


def _plan(body):
    r = S.res
    if body.get("mode") == "group":
        g = S.idr.groups[int(body["gid"]) - 1]
        keep = int(body["keep"])
        files = _remaining(g)
        if keep not in files:
            raise ValueError("The copy to keep is not part of this group.")
        return idf.plan_moves(r, S.idr, [({**g, "files": files}, keep)])
    q = {k: [str(v)] for k, v in body.get("filter", {}).items()}
    choices = [({**g, "files": f}, idf.choose_keeper(r, {**g, "files": f}, body.get("rule", "newest"),
                                                    body.get("text", "")))
               for g, f in _id_filtered(q)]
    return idf.plan_moves(r, S.idr, choices)


def start_quarantine(body):
    """dryRun -> just count; otherwise move the planned copies (never deletes)."""
    if S.idr is None:
        return {"ok": False, "error": "Run 'Find identical files' first."}
    try:
        moves = _plan(body)
    except (ValueError, KeyError, IndexError) as e:
        return {"ok": False, "error": str(e)}
    summary = {"files": len(moves), "bytes": sum(m["size"] for m in moves),
               "groups": len({m["gid"] for m in moves})}
    if body.get("dryRun"):
        return {"ok": True, **summary}
    if not moves:
        return {"ok": False, "error": "Nothing to move."}
    ctl = idf.Control()

    def fn():
        run_id, _log, done = idf.quarantine(moves, QUARANTINE_LOGS, ctl)
        S.idr.resolved.update(done)
        S.run_ids[run_id] = done
        S.job["result"] = f"Moved {len(done):,} of {len(moves):,} files to quarantine"

    return {**run_job("quarantine", fn, on_start=lambda: setattr(S, "idctl", ctl)), **summary}


def start_undo(body):
    run = os.path.basename(body.get("run", ""))
    if not os.path.exists(os.path.join(QUARANTINE_LOGS, run + ".csv")):
        return {"ok": False, "error": "Unknown quarantine run."}
    ctl = idf.Control()

    def fn():
        restored, total = idf.undo(QUARANTINE_LOGS, run, ctl)
        if S.idr is not None:
            S.idr.resolved.difference_update(S.run_ids.pop(run, []))
        S.job["result"] = f"Restored {restored:,} of {total:,} files"

    return run_job("undo", fn, on_start=lambda: setattr(S, "idctl", ctl))


def q_extensions(_q):
    """File types in the current scan (for the extension picker), biggest first."""
    if S.res is None:
        return {"exts": []}
    if getattr(S.res, "_ext_stats", None) is None:
        S.res._ext_stats = dup.extension_stats(S.res)
    return {"exts": [{"ext": e, "files": n, "bytes": b} for e, n, b in S.res._ext_stats[:60]],
            "total": len(S.res._ext_stats)}


def q_qruns(_q):
    return {"runs": idf.list_runs(QUARANTINE_LOGS)}


def a_cancel(_body):
    if S.job["kind"] == "scan" and S.scanner:
        S.scanner.cancel = True
    if S.job["kind"] in ("identical", "quarantine", "undo") and S.idctl:
        S.idctl.cancel = True
    return {"ok": True}


# ------------------------------------------------------------------ queries
def q_init(_q):
    drives = [{"root": d[0], "type": d[1], "label": d[2], "total": d[3], "used": d[4],
               "free": d[5]} for d in sc.list_drives()]
    return {
        "drives": drives,
        "systemDrive": os.environ.get("SystemDrive", "C:").upper() + "\\",
        "userFolders": sc.user_folders(),
        "cache": os.path.getmtime(CACHE) if os.path.exists(CACHE) else None,
        "outDir": OUT_DIR,
    }


def q_status(_q):
    S.last_ping = time.time()
    prog = None
    if S.job["busy"] and S.job["kind"] == "scan" and S.scanner:
        f, d, b, cur = S.scanner.progress
        prog = {"files": f, "dirs": d, "bytes": b, "current": cur}
    idprog = S.idctl.as_dict() if S.job["busy"] and S.job["kind"] in (
        "identical", "quarantine", "undo") and S.idctl else None
    return {"job": S.job, "progress": prog, "idProgress": idprog, "scan": scan_summary(),
            "dups": dup_summary(), "identical": id_summary(), "exports": S.export_log[-20:]}


def q_folder(q):
    r = S.res
    if r is None:
        return {"error": "no scan"}
    i = int(q.get("id", [r.roots[0][1]])[0])
    n = node(i, r.d_size[r.d_parent[i]] if r.d_parent[i] >= 0 else None)
    chain = []
    p = i
    while p >= 0:
        chain.append({"id": p, "name": os.path.basename(r.d_path[p].rstrip("\\"))
                      or r.d_path[p]})
        p = r.d_parent[p]
    kids = sorted(r.children(i), key=lambda c: -r.d_size[c])
    limit = 400
    children = [node(c, r.d_size[i]) for c in kids[:limit]]
    rest = kids[limit:]
    files = sorted(r.dir_files(i), key=lambda f: -r.f_size[f])[:150]
    return {
        "node": n, "drive": drive_info(r.d_path[i]), "chain": list(reversed(chain)),
        "children": children,
        "more": {"count": len(rest), "size": sum(r.d_size[c] for c in rest)} if rest else None,
        "own": {"files": r.d_own_files[i], "size": r.d_own_size[i]},
        "topFiles": [{"id": f, "name": r.f_name[f], "size": r.f_size[f],
                      "created": r.f_ctime[f], "modified": r.f_mtime[f],
                      "cloud": bool(r.f_cloud[f]),
                      "note": S.fi.describe(r.file_path(f))[0]} for f in files],
    }


def q_levels(q):
    r = S.res
    if r is None:
        return {"levels": []}
    root = q.get("root", ["all"])[0]
    rootidx = None if root == "all" else int(root)
    acc = {}
    for i, path in enumerate(r.d_path):
        if rootidx is not None and S.root_of[i] != rootidx:
            continue
        a = acc.setdefault(abs_level(path), [0, 0, 0])
        a[0] += 1
        a[1] += r.d_size[i]
        a[2] = max(a[2], r.d_size[i])
    return {"levels": [{"level": lv, "folders": a[0], "size": a[1], "largest": a[2]}
                       for lv, a in sorted(acc.items())]}


def q_level(q):
    r = S.res
    lv = int(q.get("level", ["1"])[0])
    root = q.get("root", ["all"])[0]
    rootidx = None if root == "all" else int(root)
    limit = int(q.get("limit", ["500"])[0])
    idxs = [i for i, p in enumerate(r.d_path) if abs_level(p) == lv and
            (rootidx is None or S.root_of[i] == rootidx)]
    idxs.sort(key=lambda i: -r.d_size[i])
    return {"total": len(idxs),
            "rows": [node(i, r.d_size[S.root_of[i]]) for i in idxs[:limit]]}


def _group_row(g):
    r = S.res
    return {"gid": g["gid"], "name": g["name"], "copies": g["copies"], "drives": g["drives"],
            "sameSize": g["same_size"], "distinct": g["distinct_sizes"], "total": g["total"],
            "reclaimable": g["reclaimable"], "maxLevels": g["max_levels"],
            "newest": r.f_mtime[g["newest"]]}


SORTS = {"reclaimable": lambda g: (-g["reclaimable"], -g["total"]),
         "total": lambda g: -g["total"], "copies": lambda g: -g["copies"],
         "name": lambda g: g["name"].lower(), "levels": lambda g: -g["max_levels"],
         "newest": lambda g: -S.res.f_mtime[g["newest"]]}


def q_groups(q):
    dr, r = S.dr, S.res
    if dr is None:
        return {"total": 0, "rows": []}
    g1 = lambda k, d="": q.get(k, [d])[0]  # noqa: E731
    name = g1("name").lower().strip()
    path = g1("path").lower().strip()
    match, drives = g1("match", "all"), g1("drives", "all")
    minlv = int(g1("minLevels", "0") or 0)
    offset, limit = int(g1("offset", "0")), int(g1("limit", "200"))
    out = []
    for g in dr.groups:
        if name and name not in g["name"].lower():
            continue
        if match == "same" and not g["same_size"]:
            continue
        if match == "diff" and g["same_size"]:
            continue
        if drives == "multi" and not g["multi_drive"]:
            continue
        if drives == "single" and g["multi_drive"]:
            continue
        if g["max_levels"] < minlv:
            continue
        if path and not any(path in (r.d_path[r.f_dir[f]].lower() + "\\")
                            for f in g["files"]):
            continue
        out.append(g)
    sort = g1("sort", "reclaimable")
    if sort != "reclaimable":
        out.sort(key=SORTS.get(sort, SORTS["reclaimable"]))
    return {"total": len(out),
            "reclaimable": sum(g["reclaimable"] for g in out),
            "rows": [_group_row(g) for g in out[offset:offset + limit]]}


def q_group(q):
    dr, r = S.dr, S.res
    gid = int(q.get("gid", ["1"])[0])
    g = dr.groups[gid - 1]
    copies = []
    for f in g["files"]:
        copies.append({
            "id": f, "folderId": r.f_dir[f], "path": r.file_path(f), "size": r.f_size[f],
            "created": r.f_ctime[f], "modified": r.f_mtime[f], "cloud": bool(r.f_cloud[f]),
            "levels": dr.levels[f], "rel": dr.rel[f], "root": dr.root[f],
            "rootTitle": S.fi.describe(dr.root[f])[0], "newest": f == g["newest"],
            "sameRel": dr.tail_n[f],
        })
    return {**_group_row(g), "copiesList": copies}


def q_pairs(q):
    dr = S.dr
    if dr is None:
        return {"total": 0, "rows": []}
    mn = int(q.get("min", ["10"])[0] or 1)
    rows = [p for p in dr.pairs if p["files"] >= mn]
    return {"total": len(rows), "rows": [
        {**p, "titleA": S.fi.describe(p["root_a"])[0],
         "titleB": S.fi.describe(p["root_b"])[0]} for p in rows[:2000]]}


def _inside_scan(path):
    pl = path.lower().rstrip("\\") + "\\"
    return any(pl.startswith(rp.lower().rstrip("\\") + "\\") for rp, _ in S.res.roots)


def a_open(body):
    r = S.res
    if r is None:
        return {"ok": False}
    kind = body.get("kind")
    if kind == "folder":
        p = r.d_path[int(body["id"])]
        if os.path.isdir(p):
            os.startfile(p)
    elif kind == "file":
        p = r.file_path(int(body["id"]))
        subprocess.Popen(f'explorer /select,"{p}"')
    elif kind == "root":          # backup-root paths (from pairs); must be in scan
        p = body.get("path", "")
        if _inside_scan(p) and os.path.isdir(p):
            os.startfile(p)
    elif kind == "out":
        os.makedirs(OUT_DIR, exist_ok=True)
        os.startfile(OUT_DIR)
    return {"ok": True}


def a_pick(_body):
    """Native folder picker (runs locally via PowerShell / WinForms)."""
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "$f=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
          "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
          "if($d.ShowDialog($f) -eq 'OK'){[Console]::Out.Write($d.SelectedPath)}")
    out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                         capture_output=True, text=True, creationflags=0x08000000)
    return {"path": out.stdout.strip()}


GET = {"/api/init": q_init, "/api/status": q_status, "/api/folder": q_folder,
       "/api/levels": q_levels, "/api/level": q_level, "/api/groups": q_groups,
       "/api/group": q_group, "/api/pairs": q_pairs, "/api/idgroups": q_idgroups,
       "/api/idgroup": q_idgroup, "/api/qruns": q_qruns, "/api/extensions": q_extensions}
POST = {"/api/scan": start_scan, "/api/load": start_load, "/api/dups": start_dups,
        "/api/export": start_export, "/api/open": a_open, "/api/pick": a_pick,
        "/api/cancel": a_cancel, "/api/identical": start_identical,
        "/api/quarantine": start_quarantine, "/api/undo": start_undo}
STATIC = {"/": ("index.html", "text/html"), "/app.css": ("app.css", "text/css"),
          "/app.js": ("app.js", "application/javascript")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        return secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

    def _host_ok(self):
        # DNS-rebinding guard: a hostile page rebound to 127.0.0.1 still sends
        # its own hostname in Host, so only our exact origin is served.
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        u = urlparse(self.path)
        if u.path in STATIC:
            fn, ctype = STATIC[u.path]
            with open(os.path.join(WEB, fn), "rb") as fh:
                data = fh.read()
            if fn == "index.html":
                data = data.replace(b"__TOKEN__", TOKEN.encode())
            return self._send(200, data, ctype)
        if u.path in GET:
            if not self._authed():
                return self._send(403, {"error": "forbidden"})
            try:
                return self._send(200, GET[u.path](parse_qs(u.query)))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._host_ok() or u.path not in POST or not self._authed():
            return self._send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            return self._send(200, POST[u.path](body))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._send(500, {"error": str(e)})


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def open_window(url):
    for exe in (shutil.which("msedge"),
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1500,950"])
            return
    webbrowser.open(url)


def clean_path(arg):
    """Explorer passes "C:\\" for drives, which argv parsing turns into C:" - repair it."""
    p = arg.strip().strip('"').strip()
    if len(p) == 2 and p[1] == ":":
        p += "\\"
    return p


def main():
    """Usage: dd_web.py [port] [--no-browser] [--page storage|dups|levels] [--scan <folder>]
    --scan must come last (right-click menu passes the folder as the final argument)."""
    global USE_PROCESSES
    USE_PROCESSES = True
    args = sys.argv[1:]
    scan_path = ""
    if "--scan" in args:
        i = args.index("--scan")
        scan_path = clean_path(" ".join(args[i + 1:]))
        args = args[:i]
    page = args[args.index("--page") + 1] if "--page" in args else "storage"
    nums = [a for a in args if a.isdigit()]
    port = int(nums[0]) if nums else free_port()
    ALLOWED_HOSTS.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    if scan_path:
        # right-click scans don't replace the saved whole-PC scan
        start_scan({"folders": [scan_path], "userFolders": False, "drives": [],
                    "saveCache": False})
        url += "#" + page
    print("Serving", url, "(local only). Close the window to stop.")
    if "--no-browser" not in args:
        open_window(url)

    def watchdog():
        while True:
            time.sleep(10)
            if time.time() - S.last_ping > IDLE_EXIT and not S.job["busy"]:
                srv.shutdown()
                return

    threading.Thread(target=watchdog, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
