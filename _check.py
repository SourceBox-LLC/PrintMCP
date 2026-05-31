"""Temporary: link-check all docs + flag any remaining tool-call notation in tutorials. Deleted after use."""
import re, os, unicodedata
from pathlib import Path

root = Path(".").resolve()
md = sorted(root.glob("README.md")) + sorted(root.glob("docs/**/*.md"))

_REMOVE = re.compile(r"[^\w\- ‍️]", flags=re.UNICODE)
def slug(t):
    return _REMOVE.sub("", unicodedata.normalize("NFC", t).strip().lower()).replace(" ", "-")
def k(p): return os.path.normcase(str(p.resolve()))

anchors = {}
for f in md:
    seen, uniq = {}, set()
    for line in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if not m: continue
        s = slug(m.group(2))
        if s in seen: seen[s] += 1; uniq.add(f"{s}-{seen[s]}")
        else: seen[s] = 0; uniq.add(s)
    anchors[k(f)] = uniq

link_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
broken, checked = [], 0
for f in md:
    for target in link_re.findall(f.read_text(encoding="utf-8")):
        t = target.strip()
        if t.startswith(("http://", "https://", "mailto:")): continue
        if t.startswith("#"):
            checked += 1
            if unicodedata.normalize("NFC", t[1:]) not in anchors[k(f)]:
                broken.append((f.relative_to(root).as_posix(), target, "in-page"))
            continue
        pp, _, anchor = t.partition("#")
        if not pp: continue
        checked += 1
        dest = f.parent / pp
        if not dest.exists(): broken.append((f.relative_to(root).as_posix(), target, "file")); continue
        if anchor and dest.suffix.lower() == ".md" and unicodedata.normalize("NFC", anchor) not in anchors.get(k(dest), set()):
            broken.append((f.relative_to(root).as_posix(), target, "anchor"))

# Flag tool-call notation lingering in the *tutorials* (should now be prose-first).
tutorial_smells = []
toolname = re.compile(r"\b(thingiverse_\w+|cura_slice_model|octoprint_\w+)\s*\(")
for f in sorted(root.glob("docs/tutorials/*.md")):
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if toolname.search(line):
            tutorial_smells.append(f"{f.relative_to(root).as_posix()}:{i}: {line.strip()[:80]}")

out = [f"links_checked={checked} broken={len(broken)}"]
out += [f"  [{w}] {s} -> {t}" for s, t, w in broken]
out.append(f"tutorial_toolcall_smells={len(tutorial_smells)}")
out += [f"  {s}" for s in tutorial_smells]
Path("_check_out.txt").write_text("\n".join(out), encoding="utf-8")
