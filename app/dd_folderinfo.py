"""
Offline folder descriptions (folder_descriptions.json). Nothing is looked up
online - paths never leave this PC.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
_EMPTY = ("", "", "", "")


def _translate(pat):
    """Glob -> regex where '*' / '?' stay within one path component.
    A leading '*/' means 'at any depth'; '?:' is any drive letter."""
    if pat.startswith("*/"):
        rx, pat = r"(?:.*/)?", pat[2:]
    else:
        rx = ""
    if pat.startswith("?:"):
        rx += "[a-z]:"
        pat = pat[2:]
    for ch in pat:
        rx += "[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch)
    return rx


class FolderInfo:
    def __init__(self, path=os.path.join(HERE, "folder_descriptions.json")):
        with open(path, encoding="utf-8") as fh:
            self.rules = json.load(fh)["rules"]
        alt = "|".join(f"({_translate(r[0])})" for r in self.rules)
        self.rx = re.compile(f"^(?:{alt})/?$")
        self.cache = {}

    def describe(self, path):
        """-> (title, description, category, advice) or empty strings."""
        key = path.lower().replace("\\", "/").rstrip("/")
        hit = self.cache.get(key)
        if hit is None:
            m = self.rx.match(key)
            if m:
                r = self.rules[m.lastindex - 1]
                hit = (r[1], r[2], r[3], r[4])
            else:
                hit = _EMPTY
            if len(self.cache) < 200000:
                self.cache[key] = hit
        return hit
