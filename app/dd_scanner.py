"""
Scan engine: walks folders / whole drives and keeps a compact in-memory
model of every folder (with sizes rolled up to each level) and every file.

Only metadata is read (name, size, created, modified, attributes) - file
contents are never opened. Nothing is sent anywhere. Large scans run in
parallel worker processes (see Scanner).
"""
import ctypes
import multiprocessing as mp
import os
import pickle
import shutil
import string
import threading
import time
from array import array
from concurrent.futures import (FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor,
                                as_completed, wait)

from dd_util import err_text, fix_drive, prefix

# Windows file attribute / reparse tags (junctions and symlinks are skipped)
ATTR_REPARSE = 0x400
TAG_MOUNT_POINT = 0xA0000003   # junction
TAG_SYMLINK = 0xA000000C

DRIVE_TYPES = {2: "Removable", 3: "Fixed", 4: "Network", 5: "CD/DVD", 6: "RAM disk"}


def list_drives():
    """[(letter_root, type_name, label, total, used, free)] for local drives."""
    out = []
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        dtype = k32.GetDriveTypeW(root)
        if dtype not in (2, 3, 6):
            continue
        label = ctypes.create_unicode_buffer(261)
        k32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0)
        try:
            du = shutil.disk_usage(root)
            total, used, free = du.total, du.used, du.free
        except OSError:
            continue
        out.append((root, DRIVE_TYPES.get(dtype, "?"), label.value, total, used, free))
    return out


def user_folders():
    """Desktop / Documents / Downloads / Pictures / Videos / Music - both the
    plain profile folders and the ones Windows redirects under OneDrive\\."""
    home = os.path.expanduser("~")
    out = []
    for name in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"):
        for base in (home, os.path.join(home, "OneDrive")):
            p = os.path.join(base, name)
            if os.path.isdir(p):
                out.append(p)
    return out


def normalize_roots(roots):
    """Drop roots nested inside another selected root."""
    # "Q:" must become "Q:\" BEFORE abspath - abspath("Q:") is the current
    # directory on drive Q:, not the drive root.
    norm = sorted({os.path.abspath(fix_drive(r)) for r in roots}, key=len)
    keep = []
    for r in norm:
        if not any(prefix(r).startswith(prefix(k)) for k in keep):
            keep.append(r)
    return keep


class ScanResult:
    """Folders are stored parent-before-child, so sizes roll up in reverse."""

    def __init__(self):
        # folders
        self.d_path = []                 # full path
        self.d_parent = array("i")       # parent index, -1 for a scan root
        self.d_depth = array("h")        # 0 = scan root
        self.d_own_size = array("q")     # files directly inside
        self.d_own_files = array("i")
        self.d_size = array("q")         # rolled-up totals
        self.d_files = array("q")
        self.d_dirs = array("i")
        self.d_latest = array("d")       # newest modified time in subtree
        # files
        self.f_dir = array("i")
        self.f_name = []
        self.f_size = array("q")
        self.f_ctime = array("d")
        self.f_mtime = array("d")
        # misc
        self.roots = []                  # [(root_path, folder_index)]
        self.errors = []                 # [(path, error)]
        self.skipped_links = 0
        self.started = self.finished = 0.0
        self.cancelled = False
        self._children = None
        self._dir_files = None

    # ---------- derived indexes (rebuilt after load) ----------
    def children(self, i):
        if self._children is None:
            ch = [[] for _ in self.d_path]
            for c, p in enumerate(self.d_parent):
                if p >= 0:
                    ch[p].append(c)
            self._children = ch
        return self._children[i]

    def dir_files(self, i):
        if self._dir_files is None:
            df = [[] for _ in self.d_path]
            for f, d in enumerate(self.f_dir):
                df[d].append(f)
            self._dir_files = df
        return self._dir_files[i]

    def root_of(self):
        """[scan-root folder index] for every folder (parents come first)."""
        out = [0] * len(self.d_path)
        for i, p in enumerate(self.d_parent):
            out[i] = i if p < 0 else out[p]
        return out

    def rollup(self):
        n = len(self.d_path)
        self.d_size = array("q", self.d_own_size)
        self.d_files = array("q", self.d_own_files)
        self.d_dirs = array("i", [0]) * n
        self.d_latest = array("d", [0.0]) * n
        for d, m in zip(self.f_dir, self.f_mtime):
            if m > self.d_latest[d]:
                self.d_latest[d] = m
        for i in range(n - 1, -1, -1):
            p = self.d_parent[i]
            if p >= 0:
                self.d_size[p] += self.d_size[i]
                self.d_files[p] += self.d_files[i]
                self.d_dirs[p] += self.d_dirs[i] + 1
                if self.d_latest[i] > self.d_latest[p]:
                    self.d_latest[p] = self.d_latest[i]
        self._children = None
        self._dir_files = None

    def file_path(self, f):
        return os.path.join(self.d_path[self.f_dir[f]], self.f_name[f])

    # ---------- persistence ----------
    def save(self, path):
        # copy first: the UI may add lazy "_" attributes while a background save runs
        state = {k: v for k, v in dict(self.__dict__).items() if not k.startswith("_")}
        with open(path, "wb") as fh:
            pickle.dump(state, fh, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load(cls, path):
        r = cls()
        with open(path, "rb") as fh:
            r.__dict__.update(pickle.load(fh))
        return r


# ---------------------------------------------------------------- scanning
# One folder is scanned by _scan_dir. Whole subtrees are scanned by _walk,
# either in this process (threads) or in worker processes. Results come back
# as compact column dicts ("parts") with folder indices local to the part,
# and are merged into the ScanResult by _merge. The invariant everything
# relies on - a folder's parent index is always lower than its own - holds
# inside each part (DFS) and across parts (a part's top folder hangs off a
# folder that was added before the part).

_G = {}   # per-worker-process globals set by _init_worker


def _scan_dir(folder, part, idx):
    """Scan ONE folder: append its files to `part`; return (subdirs, own_files, own_size)."""
    own_size = own_files = 0
    try:
        with os.scandir(folder) as it:
            entries = list(it)
    except OSError as e:
        part["errors"].append((folder, err_text(e)))
        return [], 0, 0
    subdirs = []
    for e in entries:
        try:
            st = e.stat(follow_symlinks=False)
            if e.is_dir(follow_symlinks=False):
                if (getattr(st, "st_file_attributes", 0) & ATTR_REPARSE and
                        getattr(st, "st_reparse_tag", 0) in (TAG_MOUNT_POINT, TAG_SYMLINK)):
                    part["links"] += 1          # junction / symlink loop guard
                    continue
                subdirs.append(e.path)
                continue
            if e.is_symlink():
                part["links"] += 1
                continue
            part["f_dir"].append(idx)
            part["f_name"].append(e.name)
            part["f_size"].append(st.st_size)
            part["f_ctime"].append(st.st_ctime)   # creation time on Windows
            part["f_mtime"].append(st.st_mtime)
            own_size += st.st_size
            own_files += 1
        except OSError as ex:
            part["errors"].append((e.path, err_text(ex)))
    return subdirs, own_files, own_size


# part column -> array typecode (None = plain list); also the ScanResult columns
_COLS = {"d_path": None, "d_parent": "i", "d_depth": "h", "d_own_size": "q",
         "d_own_files": "i", "f_dir": "i", "f_name": None, "f_size": "q",
         "f_ctime": "d", "f_mtime": "d"}


def _new_part():
    part = {k: array(t) if t else [] for k, t in _COLS.items()}
    part.update(errors=[], links=0, cancelled=False)
    return part


def _add_folder(part, folder, parent, depth):
    idx = len(part["d_path"])
    part["d_path"].append(folder)
    part["d_parent"].append(parent)
    part["d_depth"].append(depth)
    subdirs, nf, ns = _scan_dir(folder, part, idx)
    part["d_own_files"].append(nf)
    part["d_own_size"].append(ns)
    return idx, subdirs, nf, ns


def _walk(top, depth0, report=None, cancelled=None):
    """Scan the subtree under `top` (DFS). Folder indices are local: 0 = top,
    parent -1 = attach to the caller's folder. Runs in a thread or a process."""
    report = report or _G.get("report")
    cancelled = cancelled or _G.get("cancelled") or (lambda: False)
    part = _new_part()
    stack = [(top, -1, depth0)]
    pf = pd = pb = 0
    while stack:
        if cancelled():
            part["cancelled"] = True
            break
        folder, parent, depth = stack.pop()
        idx, subdirs, nf, ns = _add_folder(part, folder, parent, depth)
        pf, pd, pb = pf + nf, pd + 1, pb + ns
        if report and pd >= 200:
            report(pf, pd, pb, folder)
            pf = pd = pb = 0
        for s in reversed(subdirs):
            stack.append((s, idx, depth + 1))
    if report:
        report(pf, pd, pb, top)
    return part


def _init_worker(files, dirs, nbytes, cancel_event):
    """Worker-process initializer: shared progress counters + cancel flag."""
    def report(f, d, b, _cur):
        with files.get_lock():
            files.value += f
        with dirs.get_lock():
            dirs.value += d
        with nbytes.get_lock():
            nbytes.value += b

    _G["report"] = report
    _G["cancelled"] = cancel_event.is_set


def _merge(res, part, attach_to):
    """Append a part to res, shifting its local indices."""
    base = len(res.d_path)
    res.d_parent.extend(attach_to if p < 0 else p + base for p in part["d_parent"])
    res.f_dir.extend(d + base for d in part["f_dir"])
    for k in _COLS.keys() - {"d_parent", "f_dir"}:
        getattr(res, k).extend(part[k])
    res.errors.extend(part["errors"])
    res.skipped_links += part["links"]
    res.cancelled = res.cancelled or part["cancelled"]


class Scanner:
    """Run scan(roots) in a worker thread; poll .progress from the UI.

    Parallel strategy:
      1. the top levels (up to EXPAND_LEVELS, until there are enough
         subtrees) are scanned here, breadth-first, splitting all roots into
         many independent subtrees;
      2. subtrees are scanned in parallel - in worker PROCESSES when
         use_processes=True and there are enough of them (true parallelism:
         the per-file work is Python code, which threads can't run in
         parallel), otherwise in threads (still overlaps disk waits);
      3. parts are merged as they finish.
    use_processes must only be enabled from a program whose entry point is
    guarded by `if __name__ == "__main__":` (Windows spawns workers by
    re-importing the main module).
    """
    EXPAND_LEVELS = 3

    def __init__(self, workers=None, use_processes=False):
        self.cancel = False
        self.progress = (0, 0, 0, "")    # files, folders, bytes, current folder
        self.workers = workers or min(8, max(2, os.cpu_count() or 4))
        self.use_processes = use_processes
        self.mode = ""                   # "processes" / "threads", for display

    def scan(self, roots):
        res = ScanResult()
        res.started = time.time()
        totals = [0, 0, 0]
        lock = threading.Lock()

        def report(f, d, b, cur):
            with lock:
                totals[0] += f
                totals[1] += d
                totals[2] += b
                self.progress = (totals[0], totals[1], totals[2], cur)

        # ---- 1. breadth-first split of the top levels (scanned here)
        top = _new_part()
        level = []
        for root in normalize_roots(roots):
            if not os.path.isdir(root):
                res.errors.append((root, "not found"))
                continue
            level.append((root, -1, 0))
        target = self.workers * 6
        tasks, depth = [], 0
        while level:
            nxt = []
            for path, parent, d in level:
                if self.cancel:
                    break
                idx, subdirs, nf, ns = _add_folder(top, path, parent, d)
                report(nf, 1, ns, path)
                if parent < 0:
                    res.roots.append((path, idx))
                nxt += [(s, idx, d + 1) for s in subdirs]
            depth += 1
            level = nxt
            if len(level) >= target or depth >= self.EXPAND_LEVELS:
                tasks, level = level, []
        # `top` uses real (global) indices: it is merged first, at base 0
        _merge(res, top, -1)

        # ---- 2. subtrees in parallel
        if tasks and not self.cancel:
            procs = self.use_processes and len(tasks) >= self.workers * 2
            self.mode = "processes" if procs else "threads"
            if procs:
                self._run_processes(res, tasks, report, totals)
            else:
                self._run_threads(res, tasks, report)
        res.cancelled = res.cancelled or bool(self.cancel)
        res.rollup()
        res.finished = time.time()
        self.progress = (totals[0], totals[1], totals[2], "done")
        return res

    def _run_threads(self, res, tasks, report):
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futs = {pool.submit(_walk, p, d, report, lambda: self.cancel): parent
                    for p, parent, d in tasks}
            for fut in as_completed(futs):
                _merge(res, fut.result(), futs[fut])

    def _run_processes(self, res, tasks, report, totals):
        ctx = mp.get_context("spawn")
        files, dirs, nbytes = ctx.Value("q", 0), ctx.Value("q", 0), ctx.Value("q", 0)
        cancel_ev = ctx.Event()
        base = list(totals)
        last_done = ""
        # biggest-looking subtrees can't be known in advance; submitting in
        # path order keeps sibling folders on different workers
        with ProcessPoolExecutor(max_workers=self.workers, mp_context=ctx,
                                 initializer=_init_worker,
                                 initargs=(files, dirs, nbytes, cancel_ev)) as pool:
            futs = {pool.submit(_walk, p, d): (parent, p) for p, parent, d in tasks}
            pending = set(futs)
            while pending:
                done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                for fut in done:
                    parent, last_done = futs[fut]
                    _merge(res, fut.result(), parent)
                if self.cancel and not cancel_ev.is_set():
                    cancel_ev.set()
                self.progress = (base[0] + files.value, base[1] + dirs.value,
                                 base[2] + nbytes.value, last_done)
        totals[:] = [base[0] + files.value, base[1] + dirs.value, base[2] + nbytes.value]
