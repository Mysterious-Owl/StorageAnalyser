"""CSV exports for a ScanResult / DupResult (written to 'app output')."""
import csv
import os
from datetime import datetime


def _t(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def abs_level(path):
    return len(path.rstrip("\\").split("\\")) - 1


def _writer(path):
    fh = open(path, "w", newline="", encoding="utf-8-sig")
    return fh, csv.writer(fh)


def folder_sizes(res, fi, out_dir):
    p = os.path.join(out_dir, "folder_sizes.csv")
    fh, w = _writer(p)
    w.writerow(["level", "folder", "name", "size_bytes", "size_gb", "pct_of_parent",
                "files", "subfolders", "latest_modified",
                "what_is_it", "category", "advice", "description"])
    for i, path in enumerate(res.d_path):
        par = res.d_parent[i]
        ps = res.d_size[par] if par >= 0 else res.d_size[i]
        t, d, c, a = fi.describe(path)
        w.writerow([abs_level(path), path, os.path.basename(path.rstrip("\\")) or path,
                    res.d_size[i], round(res.d_size[i] / 1024**3, 3),
                    round(100 * res.d_size[i] / ps, 2) if ps else 0,
                    res.d_files[i], res.d_dirs[i],
                    _t(res.d_latest[i]), t, c, a, d])
    fh.close()
    return p


def level_summary(res, out_dir):
    acc = {}
    for i, path in enumerate(res.d_path):
        key = (path[:2].upper(), abs_level(path))
        a = acc.setdefault(key, [0, 0, 0])
        a[0] += 1
        a[1] += res.d_size[i]
        a[2] = max(a[2], res.d_size[i])
    p = os.path.join(out_dir, "storage_by_level.csv")
    fh, w = _writer(p)
    w.writerow(["drive", "level", "folders", "total_size_bytes", "total_size_gb",
                "largest_folder_gb"])
    for (drv, lv), a in sorted(acc.items()):
        w.writerow([drv, lv, a[0], a[1], round(a[1] / 1024**3, 3), round(a[2] / 1024**3, 3)])
    fh.close()
    return p


def inventory(res, out_dir):
    p = os.path.join(out_dir, "all_files_inventory.csv")
    fh, w = _writer(p)
    w.writerow(["drive", "folder", "file_name", "extension", "full_path", "size_bytes",
                "size_mb", "created", "modified"])
    for f in range(len(res.f_name)):
        folder = res.d_path[res.f_dir[f]]
        n = res.f_name[f]
        w.writerow([folder[:2].upper(), folder, n, os.path.splitext(n)[1].lower(),
                    os.path.join(folder, n), res.f_size[f],
                    round(res.f_size[f] / 1048576, 3), _t(res.f_ctime[f]),
                    _t(res.f_mtime[f])])
    fh.close()
    return p


def duplicates(res, dr, fi, out_dir):
    paths = []
    p = os.path.join(out_dir, "same_name_summary.csv")
    fh, w = _writer(p)
    w.writerow(["group_id", "file_name", "copies", "drives", "distinct_sizes",
                "match_type", "total_size_mb", "reclaimable_mb_if_identical",
                "max_matching_folder_levels", "newest_modified", "newest_copy_path"])
    for g in dr.groups:
        w.writerow([g["gid"], g["name"], g["copies"], g["drives"], g["distinct_sizes"],
                    "Same name + same size" if g["same_size"] else "Same name, different size",
                    round(g["total"] / 1048576, 3), round(g["reclaimable"] / 1048576, 3),
                    g["max_levels"], _t(res.f_mtime[g["newest"]]), res.file_path(g["newest"])])
    fh.close()
    paths.append(p)

    p = os.path.join(out_dir, "same_name_files.csv")
    fh, w = _writer(p)
    w.writerow(["group_id", "file_name", "copies_in_group", "match_type", "is_newest",
                "matching_folder_levels", "parent_same", "grandparent_same",
                "common_relative_path", "root", "copies_with_this_relative_path",
                "same_size_within_relative_path", "full_path", "size_bytes", "size_mb",
                "created", "modified", "root_what_is_it"])
    for g in dr.groups:
        mt = "Same name + same size" if g["same_size"] else "Same name, different size"
        for f in g["files"]:
            lv = dr.levels[f]
            w.writerow([g["gid"], res.f_name[f], g["copies"], mt,
                        "Y" if f == g["newest"] else "", lv,
                        "Y" if lv >= 1 else "N", "Y" if lv >= 2 else "N",
                        dr.rel[f], dr.root[f], dr.tail_n[f],
                        "Y" if dr.tail_same[f] else "N", res.file_path(f),
                        res.f_size[f], round(res.f_size[f] / 1048576, 3),
                        _t(res.f_ctime[f]), _t(res.f_mtime[f]),
                        fi.describe(dr.root[f])[0]])
    fh.close()
    paths.append(p)

    p = os.path.join(out_dir, "path_compare_summary.csv")
    fh, w = _writer(p)
    w.writerow(["matching_folder_levels", "files", "size_gb", "files_where_all_sizes_match"])
    for s in dr.level_summary:
        w.writerow([s["levels"], s["files"], round(s["bytes"] / 1024**3, 3), s["all_same_size"]])
    fh.close()
    paths.append(p)

    p = os.path.join(out_dir, "backup_root_pairs.csv")
    fh, w = _writer(p)
    w.writerow(["root_a", "root_b", "drives", "matching_files", "same_size_files",
                "pct_same_size", "size_gb", "typical_matching_folder_levels",
                "root_a_what_is_it", "root_b_what_is_it"])
    for r in dr.pairs:
        w.writerow([r["root_a"], r["root_b"], r["drives"], r["files"], r["same_size"],
                    r["pct_same"], round(r["bytes"] / 1024**3, 3), r["levels"],
                    fi.describe(r["root_a"])[0], fi.describe(r["root_b"])[0]])
    fh.close()
    paths.append(p)
    return paths


def identical(res, idr, out_dir):
    p = os.path.join(out_dir, "identical_files.csv")
    fh, w = _writer(p)
    w.writerow(["group_id", "blake2b_256", "size_bytes", "size_mb", "copies_in_group",
                "reclaimable_mb", "is_newest", "hard_link_of_another_copy", "moved_to_quarantine",
                "full_path", "created", "modified"])
    for g in idr.groups:
        seen = set()
        for f in g["files"]:
            link = g["linkid"].get(f)
            w.writerow([g["gid"], g["hash"], g["size"], round(g["size"] / 1048576, 3), g["copies"],
                        round(g["reclaimable"] / 1048576, 3), "Y" if f == g["newest"] else "",
                        "Y" if link in seen else "", "Y" if f in idr.resolved else "",
                        res.file_path(f), _t(res.f_ctime[f]), _t(res.f_mtime[f])])
            seen.add(link)
    fh.close()
    return p


def errors(res, out_dir):
    p = os.path.join(out_dir, "scan_errors.csv")
    fh, w = _writer(p)
    w.writerow(["path", "error"])
    w.writerows(res.errors)
    fh.close()
    return p
