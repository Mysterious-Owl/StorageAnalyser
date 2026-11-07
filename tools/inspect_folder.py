"""
Summarise a folder by metadata only (sizes, counts, extensions, dates).
File contents are never opened. Big folders are scanned with parallel
worker processes.

Usage: python tools/inspect_folder.py [--depth N] [--pause] <folder>
       (old form also works: inspect_folder.py <folder> [max_depth])
Writes: analysis output/inspect_<foldername>.csv  (every subfolder)
--pause keeps the window open at the end (used by the right-click menu).
--depth only limits what is PRINTED; the whole tree is always scanned so
sizes are complete. Set DD_OUT_DIR to write the CSV somewhere else.
"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
# these need the app folder on sys.path
import dd_scanner as sc  # noqa: E402
from dd_duplicates import extension_stats  # noqa: E402
from dd_util import csv_writer, fix_drive  # noqa: E402

OUT_DIR = os.environ.get("DD_OUT_DIR") or os.path.join(os.path.dirname(HERE), "analysis output")


def hs(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:,.0f} {u}" if u == "B" else f"{n:,.1f} {u}"
        n /= 1024


def t(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""


def parse_args(argv):
    args = list(argv)
    pause = "--pause" in args
    max_depth = 4
    if "--depth" in args:
        max_depth = int(args.pop(args.index("--depth") + 1))
    pos = [a for a in args if not a.startswith("--")]
    if len(pos) > 1 and pos[-1].isdigit():
        max_depth = int(pos.pop())
    # Explorer passes "C:\" for drives, which argv parsing turns into C:" - repair it
    return os.path.abspath(fix_drive(" ".join(pos))), max_depth, pause


def oldest_created(res):
    """Oldest file creation time in every folder's subtree (one reverse pass)."""
    oldest = [0.0] * len(res.d_path)
    for f in range(len(res.f_name)):
        d, c = res.f_dir[f], res.f_ctime[f]
        if not oldest[d] or c < oldest[d]:
            oldest[d] = c
    for i in range(len(res.d_path) - 1, 0, -1):     # children come after parents
        p = res.d_parent[i]
        if p >= 0 and oldest[i] and (not oldest[p] or oldest[i] < oldest[p]):
            oldest[p] = oldest[i]
    return oldest


def main():
    root, max_depth, pause = parse_args(sys.argv[1:])
    if pause:
        import atexit

        def _wait():
            try:
                input("\nPress Enter to close...")
            except (KeyboardInterrupt, EOFError):
                pass

        atexit.register(_wait)
    if not os.path.isdir(root):
        print(f"Folder not found: {root}")
        sys.exit(1)
    print("Scanning (metadata only)...", flush=True)
    res = sc.Scanner(use_processes=True).scan([root])
    r0 = res.roots[0][1]
    print(f"{root}\nTotal {hs(res.d_size[r0])}, {res.d_files[r0]:,} files, "
          f"{res.d_dirs[r0]:,} folders, unreadable {len(res.errors)}, "
          f"links/junctions skipped {res.skipped_links}\n")
    oldest = oldest_created(res)

    def walk(i, depth):
        rel = os.path.relpath(res.d_path[i], root) if i != r0 else "."
        print(f"{'  ' * depth}{os.path.basename(rel) if depth else rel:<45.45} "
              f"{hs(res.d_size[i]):>10} {res.d_files[i]:>8,} files  "
              f"created {t(oldest[i])} .. modified {t(res.d_latest[i])}")
        if depth >= max_depth:
            return
        kids = sorted(res.children(i), key=lambda c: -res.d_size[c])
        shown = ([c for c in kids if res.d_size[c] >= 1024 * 1024] or kids[:3])[:12]
        for c in shown:
            walk(c, depth + 1)
        if len(kids) > len(shown):
            rest = kids[len(shown):]
            print(f"{'  ' * (depth + 1)}(+{len(rest)} smaller folders, "
                  f"{hs(sum(res.d_size[c] for c in rest))})")

    walk(r0, 0)

    print("\nBy file type:")
    for e, n, s in extension_stats(res)[:15]:
        print(f"  {'.' + e if e else '(none)':<14} {n:>8,} files {hs(s):>10}")

    print("\nLargest files:")
    for f in sorted(range(len(res.f_name)), key=lambda f: -res.f_size[f])[:15]:
        print(f"  {hs(res.f_size[f]):>10}  modified {t(res.f_mtime[f])}  "
              f"{os.path.relpath(res.file_path(f), root)}")

    os.makedirs(OUT_DIR, exist_ok=True)
    name = os.path.basename(root.rstrip("\\")) or f"drive_{root[0]}"
    out = os.path.join(OUT_DIR, f"inspect_{name.replace(':', '')}.csv")
    fh, w = csv_writer(out)
    with fh:
        w.writerow(["folder", "level", "size_bytes", "size_mb", "files", "oldest_created",
                    "latest_modified"])
        for i, p in enumerate(res.d_path):
            w.writerow([p, res.d_depth[i], res.d_size[i], round(res.d_size[i] / 1048576, 2),
                        res.d_files[i], t(oldest[i]), t(res.d_latest[i])])
    print(f"\nCSV: {out}")


if __name__ == "__main__":
    main()
