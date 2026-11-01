"""
For files that share a name, compare their paths from the file upwards:
does the parent folder also match? the grandparent? ...

The longest matching tail of the path (e.g. "Projects\\X\\report.docx") is the
"common relative path"; what is left in front of it (e.g. "D:\\Backup 2023\\"
vs "Q:\\") is the "root". Roots that share many files are whole folder trees
that were copied by a backup.

Input : ../analysis output/same_name_files.csv  (from scan_and_match.py)
Outputs (in ../analysis output):
  path_compare_files.csv    - one row per same-name file with match depth
  path_compare_summary.csv  - how many files match at each folder depth
  backup_root_pairs.csv     - pairs of roots holding the same folder trees
"""
import csv
import os
from collections import defaultdict
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "analysis output")
SRC = os.path.join(OUT_DIR, "same_name_files.csv")
MAX_LEVELS = 20        # folder levels compared above the file
MAX_PAIR_GROUP = 25    # skip root pairing for tails shared by more copies


def level_label(n):
    return {0: "0 - name only (different parent folder)",
            1: "1 - same parent folder",
            2: "2 - same parent + grandparent"}.get(
        n, f"{n} - same {n} folder levels")


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    with open(SRC, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # path components, lower-cased, drive excluded from matching
    parts = []
    for r in rows:
        p = [c for c in r["full_path"].split("\\") if c]
        parts.append([c.lower() for c in p])

    # count every path tail (name, parent\name, gp\parent\name, ...)
    tail_count = defaultdict(int)
    for p in parts:
        for k in range(1, min(len(p) - 1, MAX_LEVELS + 1) + 1):
            tail_count[tuple(p[-k:])] += 1

    tail_members = defaultdict(list)
    for i, (r, p) in enumerate(zip(rows, parts)):
        depth = 1
        for k in range(2, min(len(p) - 1, MAX_LEVELS + 1) + 1):
            if tail_count[tuple(p[-k:])] >= 2:
                depth = k
            else:
                break
        r["_depth"] = depth
        r["_tail"] = tuple(p[-depth:])
        tail_members[r["_tail"]].append(i)

    out = []
    for r in rows:
        depth = r["_depth"]
        orig = [c for c in r["full_path"].split("\\") if c]
        members = [rows[j] for j in tail_members[r["_tail"]]]
        sizes = {m["size_bytes"] for m in members}
        levels = depth - 1
        out.append({
            "group_id": r["group_id"],
            "file_name": r["file_name"],
            "matching_folder_levels": levels,
            "match_level": level_label(levels),
            "parent_same": "Y" if levels >= 1 else "N",
            "grandparent_same": "Y" if levels >= 2 else "N",
            "common_relative_path": "\\".join(orig[-depth:]),
            "root": "\\".join(orig[:-depth]) + "\\",
            "copies_with_this_relative_path": len(members),
            "same_size_within_relative_path": "Y" if len(sizes) == 1 else "N",
            "copies_with_same_name": r["copies_in_group"],
            "same_size_copies": r["same_size_copies"],
            "source": r["source"],
            "full_path": r["full_path"],
            "size_bytes": r["size_bytes"],
            "size_mb": r["size_mb"],
            "created": r["created"],
            "modified": r["modified"],
        })
    out.sort(key=lambda o: (-o["matching_folder_levels"],
                            o["common_relative_path"].lower(), o["full_path"]))
    write_csv(os.path.join(OUT_DIR, "path_compare_files.csv"),
              list(out[0].keys()), out)

    # ---- summary by depth ----
    by_level = defaultdict(lambda: [0, 0, 0])
    for o in out:
        b = by_level[o["matching_folder_levels"]]
        b[0] += 1
        b[1] += int(o["size_bytes"])
        b[2] += o["same_size_within_relative_path"] == "Y"
    summary = [{"matching_folder_levels": lv, "match_level": level_label(lv),
                "files": v[0], "size_gb": round(v[1] / 1024**3, 2),
                "files_where_all_sizes_match": v[2]}
               for lv, v in sorted(by_level.items())]
    write_csv(os.path.join(OUT_DIR, "path_compare_summary.csv"),
              list(summary[0].keys()), summary)

    # ---- which roots mirror each other (parent folder must match) ----
    pairs = defaultdict(lambda: {"files": 0, "bytes": 0, "same_size": 0,
                                 "depths": defaultdict(int)})
    for tail, idx in tail_members.items():
        if len(tail) < 2 or not 2 <= len(idx) <= MAX_PAIR_GROUP:
            continue
        for a, b in combinations(idx, 2):
            ra, rb = out_root(rows[a]), out_root(rows[b])
            if ra == rb:
                continue
            key = tuple(sorted((ra, rb), key=str.lower))
            p = pairs[key]
            p["files"] += 1
            p["bytes"] += int(rows[a]["size_bytes"])
            p["same_size"] += rows[a]["size_bytes"] == rows[b]["size_bytes"]
            p["depths"][len(tail) - 1] += 1
    pair_rows = []
    for (ra, rb), p in pairs.items():
        top_depth = max(p["depths"].items(), key=lambda kv: kv[1])[0]
        pair_rows.append({
            "root_a": ra, "root_b": rb,
            "drives": f"{ra[:2]} <-> {rb[:2]}",
            "matching_files": p["files"],
            "same_size_files": p["same_size"],
            "pct_same_size": round(100 * p["same_size"] / p["files"], 1),
            "size_gb": round(p["bytes"] / 1024**3, 3),
            "typical_matching_folder_levels": top_depth,
        })
    pair_rows.sort(key=lambda r: -r["matching_files"])
    write_csv(os.path.join(OUT_DIR, "backup_root_pairs.csv"),
              list(pair_rows[0].keys()), pair_rows)

    # ---- console (aggregates only) ----
    print(f"{'folder levels matching':42} {'files':>9} {'GB':>9} {'all same size':>14}")
    for s in summary:
        print(f"{s['match_level']:42} {s['files']:>9,} {s['size_gb']:>9,.1f} "
              f"{s['files_where_all_sizes_match']:>14,}")
    print(f"\nRoot pairs sharing folder trees : {len(pair_rows):,}")
    print(f"  with 100+ shared files         : "
          f"{sum(r['matching_files'] >= 100 for r in pair_rows):,}")
    print(f"Output: {OUT_DIR}")


def out_root(r):
    orig = [c for c in r["full_path"].split("\\") if c]
    return "\\".join(orig[:-r["_depth"]]) + "\\"


if __name__ == "__main__":
    main()
