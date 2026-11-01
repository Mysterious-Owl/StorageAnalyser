"""
Inventory files on the external SSD (D:), the Data drive (Q:) and selected
C: user folders, then match files by name to spot copies left behind by
periodic backups.

Only metadata is read (name, size, created, modified) - file contents are
never opened.

Outputs (in ../analysis output):
  all_files_inventory.csv  - every file scanned
  same_name_files.csv      - every file whose name occurs 2+ times, grouped
  same_name_summary.csv    - one row per duplicated name
  scan_errors.csv          - folders that could not be read
"""
import csv
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "analysis output")

USER = os.path.expanduser("~")
ROOTS = [
    ("SSD (D:)", "D:\\"),
    ("PC - Data (Q:)", "Q:\\"),
    ("PC - C: Desktop", os.path.join(USER, "Desktop")),
    ("PC - C: Desktop", os.path.join(USER, "OneDrive", "Desktop")),
    ("PC - C: Documents", os.path.join(USER, "Documents")),
    ("PC - C: Documents", os.path.join(USER, "OneDrive", "Documents")),
    ("PC - C: Downloads", os.path.join(USER, "Downloads")),
    ("PC - C: Pictures", os.path.join(USER, "Pictures")),
    ("PC - C: Pictures", os.path.join(USER, "OneDrive", "Pictures")),
    ("PC - C: Videos", os.path.join(USER, "Videos")),
    ("PC - C: Videos", os.path.join(USER, "OneDrive", "Videos")),
]

# System / tooling folders that hold huge numbers of generic files
# (would swamp the name matching without being user data).
SKIP_DIRS = {
    "$recycle.bin", "system volume information", "recovery",
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".svn", ".hg", ".idea", ".vs",
}
# Our own folders - don't inventory ourselves.
SKIP_PATHS = {os.path.normcase(HERE), os.path.normcase(OUT_DIR)}

# Names too generic to say anything about duplication.
IGNORE_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "icon\r"}


def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def scan(source, root, rows, errors, seen):
    stack = [root]
    while stack:
        folder = stack.pop()
        key = os.path.normcase(os.path.abspath(folder))
        if key in seen or key in SKIP_PATHS:
            continue
        seen.add(key)
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError as e:
            errors.append((source, folder, str(e)))
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() not in SKIP_DIRS:
                        stack.append(e.path)
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
                # On Windows (Python < 3.12) st_ctime is the creation time.
                rows.append({
                    "source": source,
                    "drive": e.path[:2].upper(),
                    "file_name": e.name,
                    "extension": os.path.splitext(e.name)[1].lower(),
                    "folder": folder,
                    "full_path": e.path,
                    "size_bytes": st.st_size,
                    "size_mb": round(st.st_size / 1048576, 3),
                    "created": fmt_time(st.st_ctime),
                    "modified": fmt_time(st.st_mtime),
                })
            except OSError as ex:
                errors.append((source, e.path, str(ex)))


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows, errors, seen = [], [], set()
    for source, root in ROOTS:
        if not os.path.isdir(root):
            continue
        t = time.time()
        before = len(rows)
        scan(source, root, rows, errors, seen)
        print(f"{source:20} {root:45} {len(rows) - before:>9,} files  "
              f"{time.time() - t:6.1f}s", flush=True)

    inv_fields = ["source", "drive", "file_name", "extension", "folder",
                  "full_path", "size_bytes", "size_mb", "created", "modified"]
    write_csv(os.path.join(OUT_DIR, "all_files_inventory.csv"), inv_fields, rows)

    # ---- match by name (case-insensitive) ----
    groups = defaultdict(list)
    for r in rows:
        n = r["file_name"].lower()
        if n not in IGNORE_NAMES:
            groups[n].append(r)
    dup = {n: g for n, g in groups.items() if len(g) > 1}

    detail, summary = [], []
    # biggest potential waste first
    ordered = sorted(dup.items(),
                     key=lambda kv: -sum(r["size_bytes"] for r in kv[1]))
    for gid, (name, g) in enumerate(ordered, 1):
        sizes = {r["size_bytes"] for r in g}
        n_ssd = sum(r["drive"] == "D:" for r in g)
        n_pc = len(g) - n_ssd
        newest = max(g, key=lambda r: r["modified"])
        largest = max(r["size_bytes"] for r in g)
        total = sum(r["size_bytes"] for r in g)
        if len(sizes) == 1:
            verdict = "Same name + same size (likely identical)"
        else:
            verdict = "Same name, different size (different versions?)"
        where = ("SSD and PC" if n_ssd and n_pc else
                 "SSD only" if n_ssd else "PC only")
        summary.append({
            "group_id": gid, "file_name": g[0]["file_name"],
            "copies": len(g), "on_ssd": n_ssd, "on_pc": n_pc,
            "found_in": where, "distinct_sizes": len(sizes),
            "match_type": verdict,
            "total_size_mb": round(total / 1048576, 3),
            "reclaimable_mb_if_identical":
                round((total - largest) / 1048576, 3) if len(sizes) == 1 else "",
            "newest_modified": newest["modified"],
            "newest_copy_path": newest["full_path"],
        })
        size_counts = defaultdict(int)
        for r in g:
            size_counts[r["size_bytes"]] += 1
        for r in sorted(g, key=lambda r: r["modified"], reverse=True):
            detail.append({
                "group_id": gid, "copies_in_group": len(g),
                "found_in": where, "match_type": verdict,
                "same_size_copies": size_counts[r["size_bytes"]],
                "is_newest": "Y" if r is newest else "",
                **r,
            })

    write_csv(os.path.join(OUT_DIR, "same_name_files.csv"),
              ["group_id", "copies_in_group", "found_in", "match_type",
               "same_size_copies", "is_newest"] + inv_fields, detail)
    write_csv(os.path.join(OUT_DIR, "same_name_summary.csv"),
              list(summary[0].keys()) if summary else ["group_id"], summary)
    with open(os.path.join(OUT_DIR, "scan_errors.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["source", "path", "error"])
        w.writerows(errors)

    # ---- console stats (aggregates only) ----
    same = [s for s in summary if s["distinct_sizes"] == 1]
    print()
    print(f"Total files scanned        : {len(rows):,}")
    print(f"  on SSD (D:)              : {sum(r['drive'] == 'D:' for r in rows):,}")
    print(f"  on PC                    : {sum(r['drive'] != 'D:' for r in rows):,}")
    print(f"Names occurring 2+ times   : {len(summary):,} "
          f"({len(detail):,} files)")
    print(f"  same name + same size    : {len(same):,}")
    print(f"  same name, diff size     : {len(summary) - len(same):,}")
    for k in ("SSD and PC", "SSD only", "PC only"):
        print(f"  found {k:12}       : {sum(s['found_in'] == k for s in summary):,}")
    rec = sum(s["reclaimable_mb_if_identical"] or 0 for s in summary)
    print(f"Potentially reclaimable    : {rec / 1024:,.1f} GB "
          f"(extra copies of same-name+same-size files)")
    print(f"Unreadable folders/files   : {len(errors):,}")
    print(f"Output: {OUT_DIR}")


if __name__ == "__main__":
    sys.exit(main())
