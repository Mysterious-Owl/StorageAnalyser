"""CSV exports for a ScanResult / DupResult (written to 'app output')."""
import os
from datetime import datetime

from dd_util import abs_level, csv_writer, folder_name

GB, MB = 1024 ** 3, 1048576


def _t(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def _csv(out_dir, name, header, rows):
    """Write header + rows (any iterable) to out_dir/name; return the path."""
    p = os.path.join(out_dir, name)
    fh, w = csv_writer(p)
    with fh:
        w.writerow(header)
        w.writerows(rows)
    return p


def _yn(flag):
    return "Y" if flag else "N"


def match_type(g):
    return "Same name + same size" if g["same_size"] else "Same name, different size"


def level_totals(res, key=None, folders=None):
    """{key: [folders, total bytes, largest folder bytes]} over `folders`
    (default: all). key(i, path) defaults to the folder's level from the drive."""
    key = key or (lambda i, p: abs_level(p))
    acc = {}
    for i in range(len(res.d_path)) if folders is None else folders:
        a = acc.setdefault(key(i, res.d_path[i]), [0, 0, 0])
        a[0] += 1
        a[1] += res.d_size[i]
        a[2] = max(a[2], res.d_size[i])
    return acc


def folder_sizes(res, fi, out_dir):
    def rows():
        for i, path in enumerate(res.d_path):
            par = res.d_parent[i]
            ps = res.d_size[par] if par >= 0 else res.d_size[i]
            t, d, c, a = fi.describe(path)
            yield [abs_level(path), path, folder_name(path),
                   res.d_size[i], round(res.d_size[i] / GB, 3),
                   round(100 * res.d_size[i] / ps, 2) if ps else 0,
                   res.d_files[i], res.d_dirs[i], _t(res.d_latest[i]), t, c, a, d]

    return _csv(out_dir, "folder_sizes.csv",
                ["level", "folder", "name", "size_bytes", "size_gb", "pct_of_parent",
                 "files", "subfolders", "latest_modified",
                 "what_is_it", "category", "advice", "description"], rows())


def level_summary(res, out_dir):
    acc = level_totals(res, lambda i, path: (path[:2].upper(), abs_level(path)))
    return _csv(out_dir, "storage_by_level.csv",
                ["drive", "level", "folders", "total_size_bytes", "total_size_gb",
                 "largest_folder_gb"],
                ([drv, lv, a[0], a[1], round(a[1] / GB, 3), round(a[2] / GB, 3)]
                 for (drv, lv), a in sorted(acc.items())))


def inventory(res, out_dir):
    def rows():
        for f, n in enumerate(res.f_name):
            folder = res.d_path[res.f_dir[f]]
            yield [folder[:2].upper(), folder, n, os.path.splitext(n)[1].lower(),
                   os.path.join(folder, n), res.f_size[f], round(res.f_size[f] / MB, 3),
                   _t(res.f_ctime[f]), _t(res.f_mtime[f])]

    return _csv(out_dir, "all_files_inventory.csv",
                ["drive", "folder", "file_name", "extension", "full_path", "size_bytes",
                 "size_mb", "created", "modified"], rows())


def duplicates(res, dr, fi, out_dir):
    summary = _csv(
        out_dir, "same_name_summary.csv",
        ["group_id", "file_name", "copies", "drives", "distinct_sizes", "match_type",
         "total_size_mb", "reclaimable_mb_if_identical", "max_matching_folder_levels",
         "newest_modified", "newest_copy_path"],
        ([g["gid"], g["name"], g["copies"], g["drives"], g["distinct_sizes"], match_type(g),
          round(g["total"] / MB, 3), round(g["reclaimable"] / MB, 3), g["max_levels"],
          _t(res.f_mtime[g["newest"]]), res.file_path(g["newest"])] for g in dr.groups))

    def file_rows():
        for g in dr.groups:
            for f in g["files"]:
                lv = dr.levels[f]
                yield [g["gid"], res.f_name[f], g["copies"], match_type(g),
                       "Y" if f == g["newest"] else "", lv, _yn(lv >= 1), _yn(lv >= 2),
                       dr.rel[f], dr.root[f], dr.tail_n[f], _yn(dr.tail_same[f]),
                       res.file_path(f), res.f_size[f], round(res.f_size[f] / MB, 3),
                       _t(res.f_ctime[f]), _t(res.f_mtime[f]), fi.describe(dr.root[f])[0]]

    files = _csv(out_dir, "same_name_files.csv",
                 ["group_id", "file_name", "copies_in_group", "match_type", "is_newest",
                  "matching_folder_levels", "parent_same", "grandparent_same",
                  "common_relative_path", "root", "copies_with_this_relative_path",
                  "same_size_within_relative_path", "full_path", "size_bytes", "size_mb",
                  "created", "modified", "root_what_is_it"], file_rows())

    levels = _csv(out_dir, "path_compare_summary.csv",
                  ["matching_folder_levels", "files", "size_gb", "files_where_all_sizes_match"],
                  ([s["levels"], s["files"], round(s["bytes"] / GB, 3), s["all_same_size"]]
                   for s in dr.level_summary))

    pairs = _csv(out_dir, "backup_root_pairs.csv",
                 ["root_a", "root_b", "drives", "matching_files", "same_size_files",
                  "pct_same_size", "size_gb", "typical_matching_folder_levels",
                  "root_a_what_is_it", "root_b_what_is_it"],
                 ([r["root_a"], r["root_b"], r["drives"], r["files"], r["same_size"],
                   r["pct_same"], round(r["bytes"] / GB, 3), r["levels"],
                   fi.describe(r["root_a"])[0], fi.describe(r["root_b"])[0]] for r in dr.pairs))
    return [summary, files, levels, pairs]


def identical(res, idr, out_dir):
    def rows():
        for g in idr.groups:
            seen = set()
            for f in g["files"]:
                link = g["linkid"].get(f)
                yield [g["gid"], g["hash"], g["size"], round(g["size"] / MB, 3), g["copies"],
                       round(g["reclaimable"] / MB, 3), "Y" if f == g["newest"] else "",
                       "Y" if link in seen else "", "Y" if f in idr.resolved else "",
                       res.file_path(f), _t(res.f_ctime[f]), _t(res.f_mtime[f])]
                seen.add(link)

    return _csv(out_dir, "identical_files.csv",
                ["group_id", "blake2b_256", "size_bytes", "size_mb", "copies_in_group",
                 "reclaimable_mb", "is_newest", "hard_link_of_another_copy",
                 "moved_to_quarantine", "full_path", "created", "modified"], rows())


def errors(res, out_dir):
    return _csv(out_dir, "scan_errors.csv", ["path", "error"], res.errors)
