"""
Byte-identical file finder + quarantine (move, never delete) with undo.

Unlike same-name matching (dd_duplicates), this READS FILE CONTENTS - locally,
only to compute hashes. Nothing leaves the PC; only hashes are cached.

Candidates: every file at least min_size bytes, optionally limited to (or
excluding) certain extensions, outside developer/system folders, under an
optional scope folder. All files are treated as local.

Pipeline (each stage only sees files that survived the previous one):
  1. size      - files of a unique size can't have a twin           (no I/O)
  2. partial   - BLAKE2b of the first + last 64 KB                  (<=128 KB/file)
                 files <= 128 KB are read whole, so this IS their full hash
  3. full      - BLAKE2b-256 of the whole file, streamed in 1 MB chunks
  4. verify    - optional byte-by-byte comparison against the group's first file
Hard links (same file id on the same volume) are detected in the final groups:
they are one file with two names, so they free no space.

Hashes are cached per path in 'app output/hash_cache.pkl' keyed on
(size, mtime), so re-runs only read new or changed files.

Quarantine moves the chosen duplicates to
  <same drive>:\\_Duplicates (Storage Explorer)\\<run id>\\<original path>
(a rename on the same volume - instant, nothing copied) after re-checking
that size/mtime are unchanged and that the kept copy still exists. Every
move is logged to 'app output/quarantine/<run id>.csv' and can be undone.
"""
import csv
import hashlib
import os
import pickle
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import dd_duplicates as dup

EDGE = 64 * 1024                 # bytes hashed at each end in the partial stage
SMALL = 2 * EDGE                 # files up to this size are hashed whole in stage 2
CHUNK = 1024 * 1024
QUARANTINE_DIR = "_Duplicates (Storage Explorer)"


class Control:
    """Progress + cancel shared between the worker and the UI."""

    def __init__(self):
        self.cancel = False
        self.stage = ""
        self.done_files = self.total_files = 0
        self.done_bytes = self.total_bytes = 0
        self._lock = threading.Lock()

    def start(self, stage, files, nbytes):
        self.stage, self.total_files, self.total_bytes = stage, files, nbytes
        self.done_files = self.done_bytes = 0

    def add(self, nbytes=0, files=0):
        with self._lock:
            self.done_bytes += nbytes
            self.done_files += files

    def as_dict(self):
        return {"stage": self.stage, "doneFiles": self.done_files,
                "totalFiles": self.total_files, "doneBytes": self.done_bytes,
                "totalBytes": self.total_bytes}


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------ hashing
def _partial(path, size, ctl):
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb", buffering=0) as fh:
        if size <= SMALL:
            data = fh.read()
            h.update(data)
            ctl.add(len(data))
        else:
            h.update(fh.read(EDGE))
            fh.seek(-EDGE, os.SEEK_END)
            h.update(fh.read(EDGE))
            ctl.add(SMALL)
    return h.hexdigest()


def _full(path, ctl):
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb", buffering=0) as fh:
        while True:
            if ctl.cancel:
                raise Cancelled()
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
            ctl.add(len(b))
    return h.hexdigest()


def _same_bytes(a, b, ctl):
    with open(a, "rb", buffering=0) as fa, open(b, "rb", buffering=0) as fb:
        while True:
            if ctl.cancel:
                raise Cancelled()
            x, y = fa.read(CHUNK), fb.read(CHUNK)
            ctl.add(len(x) + len(y))
            if x != y:
                return False
            if not x:
                return True


class HashCache:
    """path(lower) -> (size, mtime, partial_hex, full_hex|None)."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.hits = 0
        if path and os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    self.data = pickle.load(fh)
            except Exception:  # noqa: BLE001 - a broken cache is just ignored
                self.data = {}

    def get(self, key, size, mtime):
        e = self.data.get(key)
        if e and e[0] == size and e[1] == mtime:
            return e
        return None

    def put(self, key, size, mtime, partial, full):
        self.data[key] = (size, mtime, partial, full)

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(self.data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, self.path)


class IdResult:
    def __init__(self):
        self.groups = []        # dicts, see find_identical
        self.stats = {}
        self.errors = []        # (path, error)
        self.resolved = set()   # file ids moved to quarantine since the run


DEFAULT_WORKERS = min(16, (os.cpu_count() or 4) + 4)   # I/O-bound: more threads than cores


def find_identical(res, min_size=1, skip_dev=True, skip_system=True, skip_cloud=False,
                   verify=False, scope="", workers=DEFAULT_WORKERS, ctl=None, cache_path=None,
                   include_ext=None, exclude_ext=None):
    """skip_cloud (off by default - everything is treated as local) leaves out
    files Windows marks as cloud-only placeholders, which reading would download.
    include_ext / exclude_ext: only / never these extensions (e.g. ["jpg", "png"])."""
    ctl = ctl or Control()
    t0 = time.time()
    out = IdResult()
    st = defaultdict(int)
    excl = dup.excluded_folders(res, skip_dev, skip_system)
    ext_ok = dup.make_ext_filter(include_ext, exclude_ext)
    scope_l = scope.lower().rstrip("\\") + "\\" if scope else ""

    # ---- 1. size
    by_size = defaultdict(list)
    for f in range(len(res.f_name)):
        size = res.f_size[f]
        if size < max(1, min_size) or excl[res.f_dir[f]]:
            continue
        if ext_ok and not ext_ok(res.f_name[f]):
            st["skippedExt"] += 1
            continue
        if scope_l and not (res.d_path[res.f_dir[f]].lower().rstrip("\\") + "\\").startswith(scope_l):
            continue
        if skip_cloud and res.f_cloud[f]:
            st["skippedCloud"] += 1
            continue
        by_size[size].append(f)
    cands = [f for g in by_size.values() if len(g) > 1 for f in g]
    st["considered"] = sum(len(g) for g in by_size.values())
    st["sameSizeFiles"] = len(cands)
    by_size = None

    cache = HashCache(cache_path)
    paths = {f: res.file_path(f) for f in cands}
    keys = {f: paths[f].lower() for f in cands}
    partial, full = {}, {}

    def run(fn, items, stage, nbytes):
        ctl.start(stage, len(items), nbytes)
        failed = []

        def one(f):
            if ctl.cancel:
                raise Cancelled()
            try:
                return f, fn(f)
            except Cancelled:
                raise
            except OSError as e:
                failed.append(f)
                out.errors.append((paths[f], e.strerror or str(e)))
                return f, None
            finally:
                ctl.add(files=1)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, items))
        if ctl.cancel:
            raise Cancelled()
        return {f: v for f, v in results if v is not None}

    # ---- 2. partial (cache first)
    todo = []
    for f in cands:
        e = cache.get(keys[f], res.f_size[f], res.f_mtime[f])
        if e:
            cache.hits += 1
            partial[f] = e[2]
            if e[3]:
                full[f] = e[3]
        else:
            todo.append(f)
    got = run(lambda f: _partial(paths[f], res.f_size[f], ctl), todo, "Quick check (first/last 64 KB)",
              sum(min(res.f_size[f], SMALL) for f in todo))
    partial.update(got)
    for f, h in got.items():
        small = res.f_size[f] <= SMALL
        cache.put(keys[f], res.f_size[f], res.f_mtime[f], h, h if small else None)
        if small:
            full[f] = h

    groups = defaultdict(list)
    for f, h in partial.items():
        groups[(res.f_size[f], h)].append(f)
    survivors = [f for g in groups.values() if len(g) > 1 for f in g]
    st["afterQuickCheck"] = len(survivors)

    # ---- 3. full hash
    todo = [f for f in survivors if f not in full]
    try:
        got = run(lambda f: _full(paths[f], ctl), todo, "Full hash",
                  sum(res.f_size[f] for f in todo))
    finally:
        cache.save()
    for f, h in got.items():
        full[f] = h
        cache.put(keys[f], res.f_size[f], res.f_mtime[f], partial[f], h)
    cache.save()
    st["fullyHashed"] = len(got)
    st["cacheHits"] = cache.hits

    final = defaultdict(list)
    for f in survivors:
        if f in full:
            final[(res.f_size[f], full[f])].append(f)
    final = [(k, g) for k, g in final.items() if len(g) > 1]

    # ---- 4. optional byte-by-byte verification
    if verify:
        ctl.start("Byte-by-byte check", sum(len(g) - 1 for _, g in final),
                  sum(k[0] * 2 * (len(g) - 1) for k, g in final))

        def compare(pair):                       # (reference, other) -> same?
            ref, f = pair
            try:
                return f, _same_bytes(paths[ref], paths[f], ctl)
            except OSError as e:
                out.errors.append((paths[f], e.strerror or str(e)))
                return f, None
            finally:
                ctl.add(files=1)

        pairs = [(g[0], f) for _, g in final for f in g[1:]]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            same = dict(pool.map(compare, pairs))
        if ctl.cancel:
            raise Cancelled()
        checked = []
        for k, g in final:
            keep = [g[0]] + [f for f in g[1:] if same.get(f)]
            if any(same.get(f) is False for f in g[1:]):
                st["hashCollisions"] += 1          # practically never happens
            if len(keep) > 1:
                checked.append((k, keep))
        final = checked

    # ---- hard links + group summaries
    for (size, digest), g in final:
        ids = {}
        for f in g:
            try:
                s = os.stat(paths[f])
                ids[f] = (s.st_dev, s.st_ino) if s.st_ino else ("path", f)
            except OSError:
                ids[f] = ("gone", f)
        distinct = len(set(ids.values()))
        newest = max(g, key=lambda f: res.f_mtime[f])
        drives = sorted({paths[f][:2].upper() for f in g})
        out.groups.append({
            "hash": digest, "size": size, "files": sorted(g, key=lambda f: -res.f_mtime[f]),
            "names": sorted({res.f_name[f] for f in g}, key=str.lower),
            "copies": len(g), "distinct": distinct,
            "hardlinked": distinct < len(g), "linkid": ids,
            "reclaimable": size * (distinct - 1), "newest": newest,
            "drives": ", ".join(drives), "multi_drive": len(drives) > 1,
        })
    out.groups.sort(key=lambda g: (-g["reclaimable"], -g["size"]))
    for i, g in enumerate(out.groups, 1):
        g["gid"] = i

    st["groups"] = len(out.groups)
    st["files"] = sum(g["copies"] for g in out.groups)
    st["reclaimable"] = sum(g["reclaimable"] for g in out.groups)
    st["hardlinkGroups"] = sum(g["hardlinked"] for g in out.groups)
    st["errors"] = len(out.errors)
    st["verified"] = bool(verify)
    st["threads"] = workers
    st["seconds"] = round(time.time() - t0, 1)
    st["finished"] = time.time()
    out.stats = dict(st)
    return out


# ------------------------------------------------------------------ keepers
def choose_keeper(res, g, rule="newest", text=""):
    """Pick which copy of an identical group to keep."""
    files = [f for f in g["files"]]
    t = text.lower().strip()
    path = lambda f: res.file_path(f).lower()  # noqa: E731
    if rule == "oldest":
        key = lambda f: (res.f_mtime[f], len(path(f)))  # noqa: E731
    elif rule == "shortest":
        key = lambda f: (len(path(f)), -res.f_mtime[f])  # noqa: E731
    elif rule == "prefer" and t:
        key = lambda f: (t not in path(f), -res.f_mtime[f])  # noqa: E731
    elif rule == "avoid" and t:
        key = lambda f: (t in path(f), -res.f_mtime[f])  # noqa: E731
    else:  # newest
        key = lambda f: (-res.f_mtime[f], len(path(f)))  # noqa: E731
    return min(files, key=key)


def plan_moves(res, idr, choices):
    """choices: [(group, keep_file_id)] -> list of move dicts. Hard links of
    the kept file are skipped (moving them frees nothing)."""
    moves = []
    for g, keep in choices:
        keep_link = g["linkid"].get(keep)
        for f in g["files"]:
            if f == keep or f in idr.resolved:
                continue
            if g["linkid"].get(f) == keep_link:
                continue
            moves.append({"id": f, "gid": g["gid"], "src": res.file_path(f),
                          "size": res.f_size[f], "mtime": res.f_mtime[f],
                          "keep": res.file_path(keep), "hash": g["hash"]})
    return moves


def quarantine(moves, log_dir, ctl=None):
    """Move files into <drive>\\_Duplicates (Storage Explorer)\\<run>\\... ; log every move."""
    ctl = ctl or Control()
    os.makedirs(log_dir, exist_ok=True)
    base = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_id, n = base, 1
    while os.path.exists(os.path.join(log_dir, f"{run_id}.csv")):   # two runs in one second
        n += 1
        run_id = f"{base}_{n}"
    log = os.path.join(log_dir, f"{run_id}.csv")
    ctl.start("Moving to quarantine", len(moves), sum(m["size"] for m in moves))
    done = []
    with open(log, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["status", "original", "moved_to", "size", "hash", "kept_copy", "detail"])
        for m in moves:
            if ctl.cancel:
                break
            src = m["src"]
            dest = os.path.join(src[:2] + "\\", QUARANTINE_DIR, run_id, src[3:])
            status, detail = "moved", ""
            try:
                s = os.stat(src)
                k = os.stat(m["keep"])
                if s.st_size != m["size"] or abs(s.st_mtime - m["mtime"]) > 2:
                    status, detail = "skipped", "file changed since the scan"
                elif k.st_size != m["size"]:
                    status, detail = "skipped", "kept copy changed since the scan"
                else:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    os.rename(src, dest)        # same volume -> instant, nothing copied
                    done.append(m["id"])
            except OSError as e:
                status, detail = "failed", e.strerror or str(e)
            w.writerow([status, src, dest if status == "moved" else "", m["size"],
                        m["hash"], m["keep"], detail])
            ctl.add(m["size"], 1)
    return run_id, log, done


def list_runs(log_dir):
    runs = []
    if not os.path.isdir(log_dir):
        return runs
    for fn in sorted(os.listdir(log_dir), reverse=True):
        if not fn.endswith(".csv") or fn.endswith(".undo.csv"):
            continue
        p = os.path.join(log_dir, fn)
        moved = nbytes = 0
        with open(p, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if row["status"] == "moved":
                    moved += 1
                    nbytes += int(row["size"] or 0)
        runs.append({"run": fn[:-4], "moved": moved, "bytes": nbytes,
                     "undone": os.path.exists(p[:-4] + ".undo.csv")})
    return runs


def undo(log_dir, run_id, ctl=None):
    """Move every file of a quarantine run back to where it was."""
    ctl = ctl or Control()
    log = os.path.join(log_dir, f"{run_id}.csv")
    with open(log, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.DictReader(fh) if r["status"] == "moved"]
    ctl.start("Undo", len(rows), 0)
    restored = 0
    with open(log[:-4] + ".undo.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["status", "restored_to", "from", "detail"])
        for r in rows:
            status, detail = "restored", ""
            try:
                if os.path.exists(r["original"]):
                    status, detail = "skipped", "a file already exists at the original path"
                else:
                    os.makedirs(os.path.dirname(r["original"]), exist_ok=True)
                    os.rename(r["moved_to"], r["original"])
                    restored += 1
            except OSError as e:
                status, detail = "failed", e.strerror or str(e)
            w.writerow([status, r["original"], r["moved_to"], detail])
            ctl.add(files=1)
    return restored, len(rows)
