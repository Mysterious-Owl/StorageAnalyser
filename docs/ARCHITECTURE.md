# Architecture

How Storage Explorer works inside: for anyone changing the code. For usage, see
the [README](../README.md).

## Overview

```
            ┌──────────────── app/ ────────────────┐
 drives  →  │ dd_scanner ──► ScanResult ──► dd_duplicates ──► DupResult
 folders    │                   │                 │                    │
            │                   ▼                 ▼                    │
            │            dd_folderinfo     dd_export (CSV)             │
            │                   │                                      │
            │                   │                                      │
            │   dd_web.py (HTTP + JSON API)                            │
            │      │                                                   │
            │   web/index.html · app.css · app.js  (served locally)    │
            └──────────────────────────────────────────────────────────┘
```

The engine (`dd_scanner`, `dd_duplicates`, `dd_identical`, `dd_folderinfo`, `dd_export`) has
no UI code. The front end (`dd_web` + `web/`) is a thin layer over it.
Everything uses the Python standard library only. Small helpers used
across modules (drive-letter repair, path-prefix tests, folder names,
levels, error text, the Excel-friendly CSV writer) live in `dd_util.py`.

## Engine

### `dd_scanner.py`: the scan

- **Walk:** an explicit stack (no recursion) over `os.scandir`. On Windows
  `DirEntry.stat()` comes from the directory listing itself, so there is no
  extra system call per file. File contents are never opened.
- **Parallel scan** (`Scanner`):
  1. The top levels of all roots are scanned breadth-first in the calling
     thread, at most 3 levels or until there are 6 subtrees per worker.
  2. Each remaining subtree is scanned by `_walk` into a compact *part*
     with local folder indices.
  3. Parts are merged with `_merge` as they finish, which shifts their
     indices.
  - **Processes or threads:** with `use_processes=True` and at least 2
    subtrees per worker, the parts run in a `ProcessPoolExecutor` (spawn,
    up to 8 workers). This gives real parallelism, because the per-file
    work is Python code. Otherwise a thread pool runs them, which still
    overlaps disk waits.
  - **Progress and cancel:** workers report progress through shared
    `multiprocessing.Value` counters. Cancel sets a shared `Event`, and each
    worker returns its partial part.
  - **Enabling processes:** only allowed from an entry point guarded by
    `if __name__ == "__main__"`, because Windows starts workers by
    re-importing the main script. `dd_web.main()` and
    `tools/inspect_folder.py` enable it; modules that only import
    `dd_web` (tests) stay on threads.
  - **Correctness:** `tests/test_scanner_parallel.py` checks that serial,
    threads and processes give identical results, which also match
    `os.walk`.
- **Skipped:** junctions and symlinks (reparse tags `MOUNT_POINT` / `SYMLINK`)
  are not followed, which prevents loops such as `Application Data`.
  Cloud-sync placeholders are reparse points too, but they *are* scanned.
- **All files are treated as local.** Windows' cloud-only attributes are
  not recorded. Scans saved by older versions still load; their extra
  cloud columns are ignored.
- **Data model** (`ScanResult`): parallel `array` columns instead of one
  object per file, so a 1.6 M-file PC fits in a few hundred MB.

  | Folders (`d_*`) | Files (`f_*`) |
  |---|---|
  | `d_path`, `d_parent`, `d_depth` | `f_dir` (folder index), `f_name` |
  | `d_own_size`, `d_own_files` (direct contents) | `f_size`, `f_ctime` (created), `f_mtime` |
  | `d_size`, `d_files`, `d_dirs`, `d_latest` (rolled up) | |

- **Roll-up:** folders are appended depth-first, so a parent's index is
  always lower than its children's. `rollup()` makes a single reverse pass
  that adds each folder into its parent, which yields totals at **every**
  level in O(folders).
- **Indexes:** `children(i)` and `dir_files(i)` are built lazily on first
  use and are not saved.
- **Saving:** `save()` / `load()` pickle the columns (about 155 MB for a
  whole PC, about 2 s each way).
- **Roots:** `normalize_roots` turns `Q:` into `Q:\` *before* `abspath`, and
  drops roots nested inside other roots.

### `dd_duplicates.py`: same-name matching and path comparison

0. **Extension filter** (`make_ext_filter`, shared with `dd_identical`):
   *include* = only these extensions, *exclude* = never these (exclude wins).
   `''` / `(none)` means files without an extension. `extension_stats`
   feeds the UI picker.
1. **Exclusions:** a per-folder flag is inherited from the parent. Developer
   folders (`.git`, `node_modules`, ...) and system folders (`Windows`,
   `Program Files`, `ProgramData`, `AppData`, ...) are excluded, and so are
   files below `min_size` and noise names (`desktop.ini`, `Thumbs.db`).
2. **Grouping:** files are grouped by lower-cased name, and only groups of
   two or more are kept.
3. **Path-tail matching within each group:** each copy's folder path is split
   into parts (drive excluded). The group counts every tail
   (`(parent,)`, `(grandparent, parent)`, ...). A copy's **matching folder
   levels** = the longest tail that at least one other copy also has, so
   `0` = name only, `1` = same parent, `2` = same parent and grandparent.
   This is O(copies × depth) per group and never compares every pair.
4. **Root:** the path above the matching tail (e.g. `D:\Backup 2023\` vs
   `Q:\`). Copies that share a tail but sit under different roots form a
   **root pair**. Pairs are counted across all groups, so whole copied trees
   stand out. Tails shared by more than 25 copies are not paired, to avoid
   blow-ups on generic names.
5. **Summary per group:** copies, drives, distinct sizes, total,
   *reclaimable* (total minus one copy when every size matches), newest copy,
   and maximum matching levels.

### `dd_identical.py`: byte-identical files, quarantine, undo

The only module that reads file **contents**. It reads them locally, only to
hash them. It is based on the size → hash approach of the earlier
`Q:\Scripts\photo dedup` project, with streaming, staging, caching and safe
moves added.

| Stage | Reads | Removes |
|---|---|---|
| 1. Size | nothing | every file whose size is unique |
| 2. Quick hash: BLAKE2b of first + last 64 KB (files ≤ 128 KB read whole, so that *is* their full hash) | ≤ 128 KB per file | same size, different head/tail |
| 3. Full hash: BLAKE2b-256, streamed in 1 MB chunks | whole file | same head/tail, different middle |
| 4. Optional byte-by-byte comparison against the group's first file | whole file again | (hash collisions, in theory) |

- **Candidates:** the same developer/system exclusions as same-name matching,
  plus a minimum size, an optional folder scope and an optional extension
  filter (`include_ext` / `exclude_ext`). All files are treated as local.
  The quarantine folder is always excluded.
- **Threads:** CPU cores + 4, max 16. Hashing is I/O-bound and `hashlib`
  releases the GIL on large buffers, so reads and hashing overlap. The
  quick, full and byte-compare stages all run in the pool. `Control`
  carries stage, files, bytes and cancel, which the UI polls.
- **Cache:** `hash_cache.pkl` maps `path → (size, mtime, quick, full)`. An
  entry is reused only while size and mtime are unchanged, and it is saved
  even when a run is cancelled.
- **Hard links:** in the final groups, `os.stat` gives `(st_dev, st_ino)`.
  Copies with the same file id are one file, so *reclaimable* =
  `size × (distinct file ids − 1)`, and `plan_moves` never moves a hard link
  of the kept file.
- **Keeper rules** (`choose_keeper`): newest, oldest, shortest path, path
  containing X, path not containing X. Ties are broken deterministically.
- **Quarantine:** a move is a rename to
  `<drive>\_Duplicates (Storage Explorer)\<run>\<path without drive>` on the
  same volume. It is instant, nothing is copied, and nothing is ever
  deleted. Before each move it checks that:
  - the file still has the scanned size and mtime (±2 s);
  - the kept copy still exists with the same size.

  Each move is logged to `quarantine/<run>.csv`. Run ids are unique even
  within the same second.
- **Undo:** renames each logged file back, skips any whose original path is
  taken again, and writes `<run>.undo.csv`.

### `dd_folderinfo.py` + `folder_descriptions.json`

- **Rules:** about 145 offline rules of the form `[pattern, title, description,
  category, advice]`. Nothing is looked up online.
- **Pattern syntax:** `?:` = any drive and a leading `*/` = at any depth.
  Within a pattern, `*` and `?` match inside **one** path component only, so
  `*/old*` matches `D:\Old Stuff` but not the folders underneath it.
- **Matching:** all rules compile into one alternation regex, and the first
  match wins, so put specific rules before general ones. Results are cached.
- **Categories:** System, Program, Games, App data, Cache/Temp, Developer,
  User data, Cloud sync, Backup.
- **Advice:** Keep, Safe to clear, Review, Use tool.
- The UIs show the nearest described ancestor as "inside …".

### `dd_export.py`

Each export is one `_csv(out_dir, name, header, rows)` call that streams rows
from a generator (UTF-8 with BOM so Excel opens them correctly):
`folder_sizes`, `storage_by_level`, `all_files_inventory`, `same_name_*`,
`path_compare_summary`, `backup_root_pairs`, `identical_files`, `scan_errors`.
`level_totals()` also feeds the By level page.

## Modern UI: `dd_web.py` + `web/`

### Process

`ThreadingHTTPServer` on `127.0.0.1:<free port>`, opened as an Edge app window
(`msedge --app=`), or in the default browser if Edge isn't found. One background
job runs at a time (`run_job`). The page polls `/api/status`. The server exits
90 s after the last poll, unless a job is still running.

Command line:
`dd_web.py [port] [--no-browser] [--page storage|dups|levels] [--scan <folder>]`.
`--scan` must come last: the right-click menu passes the folder as the final
argument, and drive roots arrive as `C:"` (see *Right-click menu*). Scans
started with `--scan` are not saved, so they never replace the saved
whole-PC scan.

`DD_OUT_DIR` overrides the output folder; the tests use this.

### Security model

The server holds the user's whole file listing, so:

| Threat | Defence |
|---|---|
| Other machines on the network | Binds to `127.0.0.1` only |
| Another website calling the API (CSRF) | Every `/api/*` call needs `X-Token`, a random per-run secret embedded only in the page this server serves. Other origins can't read it |
| DNS rebinding (hostile domain re-pointed to 127.0.0.1, which would make it "same origin") | Every request must carry `Host: 127.0.0.1:<port>` or `localhost:<port>`, otherwise 403. This includes `index.html`, so the token never leaks |
| Using "open" to launch arbitrary paths | `/api/open` accepts only folder or file **ids** from the current scan, or root paths inside it |
| Moving the wrong files | `/api/quarantine` takes group ids and file ids from the current identical result, never paths. It re-checks size and mtime and the kept copy before every move, only renames (never deletes), and logs everything for undo. The UI always asks for confirmation, showing a dry-run count |
| Injected content | Strict CSP (`default-src 'self'`), no external resources. All text goes through `esc()` before being added to the page |

### JSON API

All endpoints need the `X-Token` header and the correct `Host`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/init` | Drives (capacity/used/free), system drive, user folders, saved-scan time, output dir |
| GET | `/api/status` | Current job, scan progress, scan and duplicate summaries, recent exports (also the heartbeat) |
| GET | `/api/folder?id=` | Folder node, breadcrumb, children (top 400 + remainder), largest files, drive info |
| GET | `/api/levels?root=` | Folder count and total size per level |
| GET | `/api/level?level=&root=&limit=` | Biggest folders at one level |
| GET | `/api/groups?name=&path=&match=&drives=&minLevels=&sort=&offset=&limit=` | Filtered, paged same-name groups |
| GET | `/api/group?gid=` | All copies of one group with path-match details |
| GET | `/api/pairs?min=` | Backup root pairs |
| GET | `/api/idgroups?name=&path=&drives=&minMb=&sort=&offset=&limit=` | Filtered, paged identical groups (only copies not yet moved) |
| GET | `/api/idgroup?gid=&rule=&text=` | One identical group: every copy, hard-link of, moved flag, suggested keeper |
| GET | `/api/qruns` | Quarantine runs: files, bytes, undone? |
| GET | `/api/extensions` | Top 60 file types in the scan (files, bytes) for the picker |
| POST | `/api/scan` | `{drives[], userFolders, folders[], dupOptions, saveCache}` |
| POST | `/api/load` | Reopen the saved scan: `{dupOptions}` |
| POST | `/api/dups` | Re-run matching: `{minKb, skipDev, skipSystem, includeExt[], excludeExt[]}` (same options inside `dupOptions` of `/api/scan` and `/api/load`) |
| POST | `/api/export` | `{keys: [folders, levels, dups, inventory, errors]}` |
| POST | `/api/identical` | Find identical files: `{minKb, skipDev, skipSystem, verify, path, includeExt[], excludeExt[]}` |
| POST | `/api/quarantine` | `{mode: group, gid, keep}` or `{mode: bulk, rule, text, filter}`; with `dryRun: true` it only returns `{files, bytes, groups}` |
| POST | `/api/undo` | `{run}`: move a quarantine run back |
| POST | `/api/cancel` | Cancel the running scan / identical search / quarantine / undo |
| POST | `/api/open` | `{kind: folder\|file\|root\|out, id\|path}` → Explorer |
| POST | `/api/pick` | Native folder picker (PowerShell WinForms dialog) |

Starting a job while another runs returns `{"ok": false, "error": ...}` (HTTP
200). The front end's `api()` turns that into a toast.

### Front end (`web/app.js`)

Plain JavaScript, no framework. State lives in `st`. Each page has a
`RENDER[page]` function that rebuilds its section from API data. Clicks go
through one delegated listener: `[data-act]` for actions and `[data-row]` for
row navigation.

- **Polling:** a single loop. `kick()` polls now, or flags the in-flight poll
  to repeat, so loops never multiply.
- **Charts:**
  - **Treemap:** squarified layout drawn in SVG, coloured by category group.
    Uses 3 colours plus grey from the validated reference palette, with direct
    labels, a legend and a hover tooltip.
  - **Level and duplicate charts:** single-series bars.
- **Theme:** CSS variables, with dark-mode values for both
  `prefers-color-scheme` and the manual `data-theme` toggle.
- **Layout:** fixed-layout tables with container queries hide minor columns
  when a card is narrow, so tables never scroll sideways.

## Right-click menu

`add_context_menu.reg` writes `HKCU\Software\Classes\{Directory,
Directory\Background, Drive}\shell\StorageExplorer` with a `SubCommands`
submenu, like the Apple image convertor project.

- `%1` is the folder or drive that was clicked, and `%V` is the folder whose
  empty space was clicked.
- **Path always last:** Explorer quotes drive roots as `"C:\"`, and Windows
  argv parsing turns `\"` into a literal quote, so Python receives `C:"`.
  Both entry points repair this (`dd_util.fix_drive`), but only reliably when the
  path is the final argument.
- **No `wt.exe`:** the quick summary runs `python.exe` directly. Windows
  Terminal treats `;` in its command line as a tab separator, and Explorer
  inserts the path unescaped.

## Performance (this PC, 2026-11)

| Step | Size | Time |
|---|---|---|
| Scan C: + Q: (cold, old single-threaded scanner) | 1.6 M files, 0.4 M folders | ~8 min (C: dominates) |
| Scan Q: (warm): 1 worker / threads / processes | 258 k files | 2.7 s / 2.3 s / **1.5 s** |
| Scan SSD D: (warm) | 0.26 M files | ~6 s |
| Same-name matching | whole PC, default options | ~3 s |
| Save / load scan | 155 MB pickle | ~2 s / ~1 s |

## Known limitations

- **Hard links** (mostly `C:\Windows\WinSxS`) are counted once per link, so
  C: totals can exceed the space actually used.
- **Same name + same size is not proof of identical content.** Use
  *Identical files*, which hashes the contents, for proof.
- **Identical files reads every candidate file in full.** Hashing many large
  same-size files (e.g. videos copied to a backup) is bound by disk speed:
  roughly the size of the duplicates divided by the disk's read speed. Runs
  after the first only read new or changed files.
- **After quarantine, the Storage view still shows the old sizes** until you
  scan again.
- **Admin-only folders** are recorded as unreadable (`scan_errors.csv`), not
  scanned.
- **`/api/levels` and `/api/level`** recompute each folder's level from its
  path on every request. Caching it per scan would make big scans faster.
