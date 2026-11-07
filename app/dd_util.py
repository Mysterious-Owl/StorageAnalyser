"""Small helpers shared by the app modules, tools and tests (stdlib only)."""
import csv
import os


def fix_drive(arg):
    """Strip quotes/spaces; a bare drive ("C:", or C:" as Explorer's "C:\\"
    arrives through argv) becomes its root "C:\\"."""
    p = str(arg).strip().strip('"').strip()
    return p + "\\" if len(p) == 2 and p[1] == ":" else p


def prefix(path):
    """Lower-cased path ending in exactly one backslash, for prefix tests."""
    return path.lower().rstrip("\\") + "\\"


def is_under(path, root):
    """True when `path` is `root` or inside it (case-insensitive)."""
    return prefix(path).startswith(prefix(root))


def folder_name(path):
    """Last path component; a drive root keeps its full path ("C:\\")."""
    return os.path.basename(path.rstrip("\\")) or path


def abs_level(path):
    """Depth from the drive: C:\\ = 0, C:\\Users = 1, ..."""
    return len(path.rstrip("\\").split("\\")) - 1


def drives_of(paths):
    """Sorted distinct drive letters ("C:", "D:") of some paths."""
    return sorted({p[:2].upper() for p in paths})


def err_text(e):
    return e.strerror or str(e)


def csv_writer(path):
    """(file handle, csv.writer) - UTF-8 with BOM so Excel shows names correctly."""
    fh = open(path, "w", newline="", encoding="utf-8-sig")
    return fh, csv.writer(fh)
