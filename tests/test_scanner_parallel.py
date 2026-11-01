"""
Parallel scanner correctness: serial / threads / processes must give exactly
the same folders, files, sizes and roll-ups as an independent os.walk.

Usage: python tests/test_scanner_parallel.py <scratch_dir>
"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import dd_scanner as sc  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def build_tree(base):
    """~2,500 folders / ~7,500 files, uneven depth (some deep chains)."""
    n = 0
    for a in range(12):
        for b in range(12):
            for c in range(8 if (a + b) % 3 else 25):
                d = os.path.join(base, f"A{a}", f"B{b}", f"C{c}")
                if c % 7 == 0:
                    d = os.path.join(d, "deep", "deeper", "deepest")
                os.makedirs(d, exist_ok=True)
                for k in range(3):
                    with open(os.path.join(d, f"f{k}.bin"), "wb") as fh:
                        fh.write(b"x" * (k * 100 + c))
                    n += 1
    return n


def truth(root):
    dirs = files = size = 0
    for dp, dn, fn in os.walk(root):
        dirs += 1
        files += len(fn)
        size += sum(os.path.getsize(os.path.join(dp, f)) for f in fn)
    return dirs, files, size


def signature(res):
    """Order-independent description of a scan."""
    folders = {res.d_path[i].lower(): (res.d_size[i], res.d_files[i], res.d_dirs[i])
               for i in range(len(res.d_path))}
    files = sorted((res.file_path(f).lower(), res.f_size[f]) for f in range(len(res.f_name)))
    return folders, files


def main():
    scratch = os.path.abspath(sys.argv[1])
    shutil.rmtree(scratch, ignore_errors=True)
    base = os.path.join(scratch, "tree")
    os.makedirs(base)
    build_tree(base)
    other = os.path.join(scratch, "second root")
    os.makedirs(os.path.join(other, "x"))
    with open(os.path.join(other, "x", "y.txt"), "w") as fh:
        fh.write("12345")
    t_dirs, t_files, t_size = truth(base)
    o_dirs, o_files, o_size = truth(other)

    runs = {}
    for label, scanner in (("serial", sc.Scanner(workers=1)), ("threads", sc.Scanner(workers=4)),
                           ("processes", sc.Scanner(workers=4, use_processes=True))):
        t = time.time()
        res = scanner.scan([base, other])
        runs[label] = res
        r0 = dict(res.roots)[base]
        r1 = dict(res.roots)[other]
        ok = (len(res.d_path) == t_dirs + o_dirs and len(res.f_name) == t_files + o_files
              and res.d_size[r0] == t_size and res.d_files[r0] == t_files and res.d_dirs[r0] == t_dirs - 1
              and res.d_size[r1] == o_size)
        check(f"{label}: matches os.walk", ok,
              f"{len(res.d_path):,} folders {len(res.f_name):,} files mode={scanner.mode} {time.time() - t:.2f}s")
        check(f"{label}: parent index < child index",
              all(res.d_parent[i] < i for i in range(len(res.d_path))))
        check(f"{label}: every file's folder is its parent path",
              all(os.path.dirname(res.file_path(f)) == res.d_path[res.f_dir[f]].rstrip("\\")
                  for f in range(0, len(res.f_name), 97)))
    check("processes really used", runs["processes"] is not None and
          sc.Scanner(workers=4, use_processes=True).use_processes)
    sig = signature(runs["serial"])
    check("threads == serial (every folder total, every file)", signature(runs["threads"]) == sig)
    check("processes == serial (every folder total, every file)", signature(runs["processes"]) == sig)

    # cancel mid-scan returns a consistent partial result
    s = sc.Scanner(workers=4, use_processes=True)
    import threading
    threading.Timer(0.05, lambda: setattr(s, "cancel", True)).start()
    res = s.scan([base])
    check("cancel -> partial but consistent", res.cancelled and
          all(res.d_parent[i] < i for i in range(len(res.d_path))),
          f"{len(res.f_name):,} files before cancel")

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
