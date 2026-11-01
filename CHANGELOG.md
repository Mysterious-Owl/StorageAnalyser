# Changelog

## 2026-11-01: Local-only cleanup

- **"Cloud-only" removed everywhere:** the scan summary column, the folder
  detail line, the file labels and the copy badges are gone, and so are the
  `cloud_only` / `cloud_only_bytes` CSV columns. Every file is local, so
  they were always empty.
- **Leftovers deleted:** a stale bytecode file from the removed classic UI.

## 2026-11-01: File types, local-only, parallelism

- **File types picker** on Same-name files (and Scan) and on Identical
  files:
  - **Modes:** All types / Only these / All except these.
  - **Presets:** Photos, Videos, Audio, Documents, Archives.
  - **Your scan's biggest types:** click to toggle, or type extensions.
  - **API:** new `/api/extensions`; `includeExt` / `excludeExt` options.
- **Everything is treated as local:** the OneDrive "skip cloud-only" option
  is removed from Identical files.
- **Parallel scanning:** subtrees are scanned in worker processes (up to 8)
  across all drives, and small scans use threads. On Q: (warm cache) this
  is 1.8× faster than a single worker.
- **Identical files:** 16 hashing threads (cores + 4, max 16), and the
  byte-by-byte check is now parallel too.
- **Smaller speed-ups:**
  - The scan is saved in the background while same-name matching runs.
  - Exports are written concurrently.
  - `inspect_folder.py` scans in parallel, and its "oldest file" pass is now
    a single linear pass.
- **Tests:**
  - New `test_scanner_parallel.py`: serial = threads = processes = `os.walk`,
    plus cancel.
  - `test_identical.py` now has 40 checks, including extension filters.
  - `bench_whole_pc.py` compares the three scan modes.

## 2026-11-01: Byte-identical files

- **New page, Identical files** (`app/dd_identical.py`): finds files with
  exactly the same bytes, whatever their name. It narrows the candidates by
  size, then a quick hash of the first and last 64 KB, then a full streamed
  BLAKE2b-256 hash. A byte-by-byte comparison is optional. Based on the
  approach in `Q:\Scripts\photo dedup`.
- **Fast re-runs:** a hash cache (`app output/hash_cache.pkl`) means later
  runs only read new or changed files.
- **Hard links detected:** they are counted once, so they never inflate
  "can free", and they are never moved.
- **Cloud-only files skipped by default:** OneDrive cloud-only files aren't
  read unless you turn this off.
- **Keepers:** choose by hand in each group's panel, or by rule (newest,
  oldest, shortest path, path containing / not containing some text) for
  all filtered groups.
- **Quarantine instead of delete:** the other copies are moved (renamed) to
  `X:\_Duplicates (Storage Explorer)\<run>\…` on the same drive. Each move
  re-checks the file and the kept copy first, every run is logged, and
  **Undo** puts the files back.
- **Exports:** new `identical_files.csv`.
- **Tests:** new `tests/test_identical.py` (30 checks).

## 2026-11-01: Classic UI removed

- **Removed** the Tkinter app (`app/dd_app.py`), its launcher
  `Storage Explorer (classic).bat` and `tests/test_app_smoke.py`. The modern
  UI covers every feature, and `test_web_smoke.py` covers the engine and
  exports. Tkinter is no longer required.

## 2026-11-01: Code review fixes

- **Security:** the web server now rejects requests whose `Host` isn't
  `127.0.0.1:<port>` / `localhost:<port>`. This blocks DNS-rebinding pages
  from reading the token and the file listing.
- **The saved scan is protected.** A cancelled (partial) scan no longer
  overwrites `last_scan.pkl` (web and classic UI).
- **Busy jobs report an error.** Starting a scan, matching or export while
  another job runs now shows an error, instead of a false "started" message.
- **Cancel works on the right scan.** A refused second scan no longer replaces
  the running scan's progress reporting, so Cancel still works.
- **Load last scan uses your options.** It now applies the matching options
  shown on the Scan page.
- **Classic UI recovers from failures.** A failing job (e.g. exporting while
  the CSV is open in Excel) shows an error instead of locking every button.
- **Drive letters are handled correctly.** A bare `Q:` entered as a folder now
  means the drive root, not Q:'s current directory.
- **Right-click quick summary no longer uses `wt.exe`.** Folders with `;` in
  their name broke Windows Terminal's command line.
- **No duplicate polling loops.** The page now keeps a single status loop.
- **Tests:** `test_context_menu.py` uses a `subst` drive instead of scanning
  all of Q:. New checks cover the Host guard, busy jobs, cancelled scans,
  `normalize_roots`, and failing jobs in the classic UI.

## 2026-11-01: Right-click menu

- **New submenu:** Explorer gets **Storage Explorer ▸** (Show storage / Find
  same-name files / Storage by level / Quick size summary) on folders, folder
  background and drives. It's added with `add_context_menu.reg` and removed
  with `remove_context_menu.reg`.
- **New command-line options:** `dd_web.py --page <page> --scan <folder>`
  (scans opened this way are not saved), and `inspect_folder.py --depth N
  --pause`.

## 2026-11-01: Project layout

- **Folders:** files split into `app/`, `app output/`, `tools/`,
  `analysis/`, `analysis output/`, `tests/`, `temp/`.
- **New files:** `README.md`, `requirements.txt`, `.gitignore` (outputs are
  never committed), and root launchers.

## 2026-11-01: Modern UI

- **Local web app** (`dd_web.py` + `web/`): drive cards, treemap drill-down,
  storage by level, same-name groups with a details panel per group, backup
  roots, exports, and dark/light themes. Works fully offline.
- **Contents table** fits its card without scrolling sideways.

## 2026-11-01: Desktop app

- **Tkinter app** with scanning of a whole PC, drives or folders; folder
  sizes at every level; and offline folder descriptions.
- **Engine** split into `dd_scanner`, `dd_duplicates`, `dd_folderinfo`,
  `dd_export`.

## 2026-10-20: First analysis

- **`scan_and_match.py`:** inventory of the SSD (D:), Q: and the C: user
  folders, with same-name matching.
- **`compare_paths.py`:** parent / grandparent path matching and backup
  root pairs.
