"""Scan speed: serial vs threads vs processes. Prints aggregates only.

Usage: python tests/bench_whole_pc.py [root ...]     (default: every local drive)
Each mode runs once after a warm-up pass, so the OS metadata cache is equally
warm for all of them (first-ever scans after a reboot are slower for all modes).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import dd_duplicates as dup  # noqa: E402
import dd_scanner as sc  # noqa: E402


def run(label, scanner, roots):
    t = time.time()
    res = scanner.scan(roots)
    secs = time.time() - t
    print(f"{label:<28} {len(res.f_name):>10,} files {len(res.d_path):>9,} folders "
          f"{sum(res.d_size[i] for _, i in res.roots) / 1024**3:>8,.1f} GB  {secs:6.1f}s  "
          f"({scanner.mode or 'n/a'})", flush=True)
    return res, secs


def main():
    roots = sys.argv[1:] or [d[0] for d in sc.list_drives()]
    print("roots:", roots)
    run("warm-up (threads)", sc.Scanner(), roots)
    _, t1 = run("1 worker (serial)", sc.Scanner(workers=1), roots)
    _, t2 = run("threads", sc.Scanner(), roots)
    res, t3 = run("processes", sc.Scanner(use_processes=True), roots)
    print(f"speed-up vs serial: threads x{t1 / t2:.1f}, processes x{t1 / t3:.1f}")
    t = time.time()
    dr = dup.build(res, 1024, True, True)
    print(f"same-name matching: {len(dr.groups):,} groups in {time.time() - t:.1f}s")


if __name__ == "__main__":
    main()
