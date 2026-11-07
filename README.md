# Storage Explorer (data dedup)

A Windows tool to see where disk space goes and to find files that exist in
more than one place, typically left behind by periodic backups to an
external SSD.

- **Storage**: size of every folder at every level, a clickable treemap,
  and plain-English descriptions of common Windows / app / developer
  folders with advice (*Keep*, *Safe to clear*, *Review*, *Use tool*).
- **Storage by level**: total size at each folder depth from the drive,
  and the biggest folders at any level.
- **Same-name files**: files that share a name, compared by size and by
  how much of their path matches (same parent folder? grandparent? ...).
- **Identical files**: byte-for-byte identical files, whatever their name
  (size, then a quick hash, then a full BLAKE2b-256 hash, optionally a
  byte-by-byte comparison). Pick a keeper per group or by rule, then move
  the other copies to a quarantine folder. You can undo any run.
- **Backup roots**: whole folder trees that exist in two places.
- **Export**: CSVs of everything.
- **Right-click menu** in Explorer for any folder or drive.

> **Privacy.** Scanning reads only file metadata: names, sizes, created
> and modified dates, and attributes. File **contents** are read only by
> *Identical files*, on this PC, to compute hashes. Only the hashes are
> kept (`app output/hash_cache.pkl`). Nothing is sent to the internet. The app is served from `127.0.0.1`
> only and loads no external resources. Every request needs a per-run token
> and the exact local `Host`, which blocks other websites and DNS-rebinding
> tricks. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#security-model).

## Run

Nothing to install (see `requirements.txt`): Python 3.10+ on Windows.

| What | How |
|---|---|
| App | double-click **`Storage Explorer.bat`**, or `python app/dd_web.py` |
| Inspect one folder | `python tools/inspect_folder.py [--depth N] [--pause] "<folder>"` |
| Right-click menu | double-click **`add_context_menu.reg`** (remove with `remove_context_menu.reg`) |

The app opens in an Edge app window. Its server stops about 90 seconds
after the window is closed. A finished scan is saved to
`app output/last_scan.pkl` and can be reopened with **Load last scan**.
Cancelled scans are never saved, so they can't replace a complete one.

### Using the app

1. **Scan:** click drive cards (or **Whole PC**), keep or untick
   *Include my user folders*, add extra folders, then **Start scan**.
   Same-name matching runs automatically afterwards.
2. **Storage:** click treemap boxes or table rows to drill down, and use
   the breadcrumb to go back up. The side panel says what the folder is, how
   full the drive is, and lists the largest files.
3. **By level:** click a level bar to list the biggest folders at that depth.
4. **Same-name files:** choose which file types to match (see *File
   types* below) and click **Re-run matching**. Filter by name, path, size match, drives or
   minimum matching levels. Click a row to see every copy: the shared part
   of the path is **bold**, and the newest copy is outlined.
5. **Identical files:** optionally limit the run to certain file types,
   then click **Find identical files**. All files are treated as local.
   - **Pick keepers:** open a group to choose the copy to keep, or set a
     rule (newest, oldest, shortest path, path containing / NOT containing
     some text, e.g. avoid `Backup`) for every group in the current filter.
   - **Move the rest:** the other copies are **moved, not deleted**, to
     `X:\_Duplicates (Storage Explorer)\<run>\<original path>` on the same
     drive. This is an instant rename, and nothing is copied.
   - **Safety:** a file is skipped if it changed since the scan, or if the
     kept copy is gone or changed. Hard links (the same file under two
     names) are never moved, because that frees nothing.
   - **Undo:** every run is listed under *Quarantine runs* with an **Undo**
     button, and logged in `app output/quarantine/<run>.csv`. Once you're
     sure, delete the run folder yourself.
6. **Backup roots:** folder pairs that hold the same trees. Use the icons
   to open either side or list its same-name files.
7. **Export:** CSVs to `app output/`.

### File types

*Same-name files* (and the Scan page, which runs it) and *Identical files*
each have a **File types** picker:

- **Modes:** *All types*, *Only these*, or *All except these*.
- **Presets:** one-click **Photos**, **Videos**, **Audio**, **Documents**
  and **Archives** extension lists. The photo and video lists come from the
  photo dedup project.
- **Your scan's file types:** the 40 biggest are shown with their total size.
  Click to add or remove one, or type extensions (`jpg, png`) and press Enter.
  `(no extension)` matches files without one.

### Speed

- **Scanning uses up to 8 parallel worker processes.** The top folder levels are
  split into independent subtrees, which are scanned at the same time, on
  every drive at once. Small scans use threads instead.
  - **Measured on Q:** (258 k files, warm cache), compared with a single
    worker: threads 1.2× faster, processes 1.8× faster.
- **Identical files hashes with 16 threads** on this PC (CPU cores + 4,
  max 16). Hashing releases
  Python's GIL, so reads and hashing overlap. The byte-by-byte check is
  parallel too.
- **Saving the scan runs in the background** while same-name matching runs.
- **Exports are written concurrently.**

### Right-click menu

`add_context_menu.reg` adds a **Storage Explorer ▸** submenu (current user
only) when you right-click a folder, the empty space inside a folder, or a
drive in *This PC*. On Windows 11 it is under **Show more options** (or
Shift+right-click).

| Item | Does |
|---|---|
| Show storage | Opens the app, scans that folder, shows the treemap |
| Find same-name files | Same, opens on the duplicates page |
| Storage by level | Same, opens on the levels page |
| Quick size summary (terminal) | Runs `tools/inspect_folder.py` in a console; writes `analysis output/inspect_<name>.csv` |

- **Not saved:** right-click scans never replace the saved whole-PC scan.
- **Absolute paths:** the `.reg` files contain `Q:\Scripts\data dedup` and
  `C:\Program Files\Python310`. If either moves, edit them and import again.
- **No `wt.exe`:** the quick summary starts `python.exe` directly because
  Windows Terminal's command line breaks on folder names containing `;`.
  On Windows 11 it still opens in Windows Terminal if that is your default
  terminal.

## Folder layout

| Folder | Category | Contents |
|---|---|---|
| `app/` | Actual code | The application (see below) |
| `app output/` | Actual code outputs | CSV exports and the saved scan from the app *(git-ignored)* |
| `tools/` | Useful utilities | `inspect_folder.py`: size/date breakdown of any folder |
| `analysis/` | Analysis | One-off scripts from the first investigation (SSD vs PC) |
| `analysis output/` | Analysis outputs | CSVs from `analysis/` and `tools/` *(git-ignored)* |
| `tests/` | Other | Smoke tests and a whole-PC benchmark |
| `docs/` | Docs | [Architecture & developer guide](docs/ARCHITECTURE.md) |
| `temp/` | Temp | Scratch space *(git-ignored)* |

Root files: launchers (`*.bat`), right-click menu (`*.reg`), `README.md`,
[`CHANGELOG.md`](CHANGELOG.md), `requirements.txt`, `.gitignore`.

### `app/`

| File | Role |
|---|---|
| `dd_scanner.py` | Walks drives/folders (parallel worker processes) into a compact in-memory model; rolls sizes up every level; skips junctions/symlinks |
| `dd_duplicates.py` | Same-name grouping, path-tail matching (parent / grandparent / N levels), backup root pairs, extension filters |
| `dd_identical.py` | Byte-identical finder (size → quick hash → BLAKE2b-256 → optional byte compare, multi-threaded), hash cache, hard-link detection, keeper rules, quarantine + undo |
| `dd_folderinfo.py` + `folder_descriptions.json` | Offline descriptions of ~145 common folders (edit the JSON to add more) |
| `dd_export.py` | CSV writers |
| `dd_web.py` + `web/` | The app: local HTTP server, JSON API and HTML/CSS/JS front end |
| `dd_util.py` | Small shared helpers (drive-letter repair, path tests, CSV writer) |

### `analysis/`

| Script | Output (in `analysis output/`) |
|---|---|
| `scan_and_match.py` | `all_files_inventory.csv`, `same_name_files.csv`, `same_name_summary.csv`, `scan_errors.csv`. Scans SSD D:, Data Q: and the C: user folders |
| `compare_paths.py` | `path_compare_files.csv`, `path_compare_summary.csv`, `backup_root_pairs.csv`. Reads the output above |

### Exports (`app output/`)

| File | Contents |
|---|---|
| `folder_sizes.csv` | Every folder: level, size, % of parent, files, sub-folders, newest change, what it is, advice |
| `storage_by_level.csv` | Per drive and level: folder count, total size, largest folder |
| `same_name_summary.csv` | One row per repeated name: copies, drives, sizes, reclaimable, newest copy |
| `same_name_files.csv` | Every copy: matching levels, parent/grandparent same, shared relative path, root |
| `path_compare_summary.csv` | Files and size per matching-level bucket |
| `backup_root_pairs.csv` | Root A / Root B, shared files, % same size, size |
| `identical_files.csv` | Every byte-identical copy: group, BLAKE2b-256 hash, size, hard-link / moved flags, dates |
| `quarantine/<run>.csv` | Log of each quarantine run (original, moved_to, kept copy, status); `<run>.undo.csv` after an undo |
| `hash_cache.pkl` | Cached hashes (path, size, modified date → hash) so re-runs are fast |
| `all_files_inventory.csv` | Every file (large on a whole-PC scan) |
| `scan_errors.csv` | Folders that could not be read |

## Reading the results

- **Matching folder levels**: `0` = only the file name matches; `1` = same
  parent folder; `2` = same parent and grandparent; deeper = a whole folder
  tree was copied (very likely a backup).
- **Same name + same size** is strong evidence of a duplicate, not proof.
  Use **Identical files** for proof: it compares the actual bytes.
- **Identical files' "Can free"** counts each hard-linked file once, because
  removing a hard link frees no space.
- **Reclaimable** = total size of a same-name, same-size group minus one copy.
- **Used, not visible** on a drive = space the scan can't see (protected
  system folders, restore points, page/hibernate files).
- **Hard links:** `C:\Windows\WinSxS` uses hard links, so the C: scan can
  count more than the drive actually uses.
- **Speed:** a whole-C: scan takes several minutes (about 1.4 M files took
  about 8 minutes).

## Tests

```
python tests/test_web_smoke.py   "%TEMP%\dd_webtest"   # backend + JSON API on a synthetic tree
python tests/test_context_menu.py "%TEMP%\dd_ctxtest"  # right-click command lines, drive roots (via subst),
                                                       #   Host guard, busy jobs, cancelled scans
python tests/test_identical.py   "%TEMP%\dd_idtest"    # identical finder, hard links, cache, keeper rules,
                                                       #   quarantine + undo, API end-to-end (via subst)
python tests/test_scanner_parallel.py "%TEMP%\dd_partest" # serial = threads = processes = os.walk, cancel
python tests/bench_whole_pc.py [Q:\]                   # scan speed: serial vs threads vs processes
```

The tests build their own synthetic folders and write only to the given temp
folder (via `DD_OUT_DIR`), never to `app output/` or `analysis output/`.
