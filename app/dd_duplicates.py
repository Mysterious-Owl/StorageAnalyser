"""
Same-name matching + path comparison on a ScanResult.

For each name that occurs 2+ times we work out, per copy, how many parent
folder levels also match another copy (0 = name only, 1 = same parent,
2 = same parent + grandparent, ...). The unmatched front part of the path is
the copy's "root"; roots that share many trees are backup copies of each other.
"""
import os
from collections import defaultdict
from itertools import combinations

MAX_LEVELS = 15
MAX_PAIR_GROUP = 25

IGNORE_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "icon\r", "ehthumbs.db"}
DEV_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".svn",
            ".hg", ".idea", ".vs", ".gradle", ".m2", ".nuget", ".tox",
            "site-packages", ".mypy_cache", ".pytest_cache", "bower_components"}
SYSTEM_TOP = {"windows", "program files", "program files (x86)", "programdata",
              "$recycle.bin", "system volume information", "recovery",
              "$windows.~bt", "$windows.~ws", "windows.old", "$winreagent",
              "msocache", "perflogs", "$sysreset", "onedrivetemp", "intel",
              "amd", "nvidia", "drivers", "config.msi"}
QUARANTINE_NAME = "_duplicates (storage explorer)"   # see dd_identical.QUARANTINE_DIR
SYSTEM_ANY = {"appdata", "$recycle.bin", "system volume information"}


class DupResult:
    def __init__(self):
        self.groups = []          # list of dicts, see build()
        self.levels = {}          # file idx -> matching folder levels
        self.root = {}            # file idx -> root path
        self.rel = {}             # file idx -> common relative path
        self.tail_n = {}          # file idx -> copies sharing that relative path
        self.tail_same = {}       # file idx -> all those copies same size?
        self.pairs = []           # root pair rows
        self.level_summary = []   # rows by level
        self.excluded_files = 0
        self.ext_skipped = 0      # files left out by the extension filter


def norm_ext(e):
    """'.JPG' / 'jpg ' -> 'jpg'; '(none)' or '' -> '' (files without an extension)."""
    e = str(e).strip().lower().lstrip(".").strip()
    return "" if e in ("(none)", "none", "-") else e


def file_ext(name):
    return os.path.splitext(name)[1].lower().lstrip(".")


def make_ext_filter(include=None, exclude=None):
    """-> predicate(file_name) or None when no extension filter is set.
    include: only these extensions; exclude: never these. Both may be given."""
    inc = {norm_ext(e) for e in include or [] if str(e).strip()}
    exc = {norm_ext(e) for e in exclude or [] if str(e).strip()}
    if not inc and not exc:
        return None

    def ok(name):
        e = file_ext(name)
        return (not inc or e in inc) and e not in exc

    return ok


def extension_stats(res):
    """[(ext, files, bytes)] for every extension in the scan, biggest first."""
    acc = defaultdict(lambda: [0, 0])
    for f, name in enumerate(res.f_name):
        a = acc[file_ext(name)]
        a[0] += 1
        a[1] += res.f_size[f]
    return sorted(((e, a[0], a[1]) for e, a in acc.items()), key=lambda t: -t[2])


def excluded_folders(res, exclude_dev, exclude_system):
    """Per-folder flag (bytearray): 1 = folder is inside a developer / system
    folder that matching should skip. Inherited parent -> child."""
    return _excluded_folders(res, exclude_dev, exclude_system)


def _excluded_folders(res, exclude_dev, exclude_system):
    n = len(res.d_path)
    ex = bytearray(n)
    root_is_drive = {}
    for rp, ri in res.roots:
        root_is_drive[ri] = len(rp.rstrip("\\")) == 2
    top_root = [0] * n
    for i in range(n):
        p = res.d_parent[i]
        if p < 0:
            top_root[i] = i
            continue
        top_root[i] = top_root[p]
        if ex[p]:
            ex[i] = 1
            continue
        name = res.d_path[i].rsplit("\\", 1)[-1].lower()
        if name == QUARANTINE_NAME:     # our own quarantine: always copies of kept files
            ex[i] = 1
        elif exclude_dev and name in DEV_DIRS:
            ex[i] = 1
        elif exclude_system and (name in SYSTEM_ANY or (
                res.d_depth[i] == 1 and root_is_drive.get(top_root[i])
                and name in SYSTEM_TOP)):
            ex[i] = 1
    return ex


def build(res, min_size=0, exclude_dev=True, exclude_system=True, progress=None,
          include_ext=None, exclude_ext=None):
    out = DupResult()
    ex = _excluded_folders(res, exclude_dev, exclude_system)
    ext_ok = make_ext_filter(include_ext, exclude_ext)

    by_name = defaultdict(list)
    for f in range(len(res.f_name)):
        if ex[res.f_dir[f]]:
            out.excluded_files += 1
            continue
        if res.f_size[f] < min_size:
            continue
        if ext_ok and not ext_ok(res.f_name[f]):
            out.ext_skipped += 1
            continue
        nm = res.f_name[f].lower()
        if nm not in IGNORE_NAMES:
            by_name[nm].append(f)
    dups = [g for g in by_name.values() if len(g) > 1]
    by_name = None

    comp_cache = {}

    def comps(d):
        c = comp_cache.get(d)
        if c is None:
            c = tuple(x for x in res.d_path[d].lower().split("\\")[1:] if x)
            comp_cache[d] = c
        return c

    pair_acc = defaultdict(lambda: [0, 0, 0, defaultdict(int)])
    lvl_acc = defaultdict(lambda: [0, 0, 0])

    for gi, g in enumerate(dups):
        if progress and gi % 2000 == 0:
            progress(gi, len(dups))
        # ---- path tail matching within the group ----
        cnt = defaultdict(int)
        fc = {f: comps(res.f_dir[f]) for f in g}
        for f in g:
            c = fc[f]
            for k in range(1, min(len(c), MAX_LEVELS) + 1):
                cnt[c[-k:]] += 1
        tails = defaultdict(list)
        for f in g:
            c = fc[f]
            lv = 0
            for k in range(1, min(len(c), MAX_LEVELS) + 1):
                if cnt[c[-k:]] >= 2:
                    lv = k
                else:
                    break
            out.levels[f] = lv
            tails[(c[-lv:] if lv else ())].append(f)
        for tail, members in tails.items():
            sizes = {res.f_size[m] for m in members}
            for m in members:
                lv = out.levels[m]
                folder = res.d_path[res.f_dir[m]]
                parts = folder.rstrip("\\").split("\\")
                keep = parts[:len(parts) - lv] if lv else parts
                out.root[m] = "\\".join(keep) + "\\"
                out.rel[m] = "\\".join(parts[len(parts) - lv:] + [res.f_name[m]]) if lv \
                    else res.f_name[m]
                out.tail_n[m] = len(members)
                out.tail_same[m] = len(sizes) == 1
                a = lvl_acc[lv]
                a[0] += 1
                a[1] += res.f_size[m]
                a[2] += len(sizes) == 1 and len(members) > 1
            if tail and 2 <= len(members) <= MAX_PAIR_GROUP:
                for a, b in combinations(members, 2):
                    ra, rb = out.root[a], out.root[b]
                    if ra.lower() == rb.lower():
                        continue
                    key = tuple(sorted((ra, rb), key=str.lower))
                    p = pair_acc[key]
                    p[0] += 1
                    p[1] += res.f_size[a]
                    p[2] += res.f_size[a] == res.f_size[b]
                    p[3][len(tail)] += 1

        # ---- group summary ----
        sizes = [res.f_size[f] for f in g]
        distinct = len(set(sizes))
        newest = max(g, key=lambda f: res.f_mtime[f])
        drives = sorted({res.d_path[res.f_dir[f]][:2].upper() for f in g})
        total = sum(sizes)
        out.groups.append({
            "name": res.f_name[g[0]],
            "files": sorted(g, key=lambda f: -res.f_mtime[f]),
            "copies": len(g),
            "drives": ", ".join(drives),
            "multi_drive": len(drives) > 1,
            "distinct_sizes": distinct,
            "same_size": distinct == 1,
            "total": total,
            "reclaimable": total - max(sizes) if distinct == 1 else 0,
            "max_levels": max(out.levels[f] for f in g),
            "newest": newest,
        })

    out.groups.sort(key=lambda r: (-r["reclaimable"], -r["total"]))
    for i, grp in enumerate(out.groups, 1):
        grp["gid"] = i

    for (ra, rb), p in pair_acc.items():
        top = max(p[3].items(), key=lambda kv: kv[1])[0]
        out.pairs.append({
            "root_a": ra, "root_b": rb, "drives": f"{ra[:2]} <-> {rb[:2]}",
            "files": p[0], "same_size": p[2],
            "pct_same": round(100 * p[2] / p[0], 1), "bytes": p[1],
            "levels": top,
        })
    out.pairs.sort(key=lambda r: -r["files"])

    for lv in sorted(lvl_acc):
        a = lvl_acc[lv]
        out.level_summary.append({"levels": lv, "files": a[0], "bytes": a[1],
                                  "all_same_size": a[2]})
    if progress:
        progress(len(dups), len(dups))
    return out
