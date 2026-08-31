"""Break the code six ways; the test suite must go red each time.
A mutation the suite survives means a test that cannot fail, and a test
that cannot fail is noise.

Run: python mutcheck.py   (exit 1 if any mutant survives, CI-ready)
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

SRC = pathlib.Path(__file__).resolve().parent

MUTS = [
    ("pipeline.py", "it.trust = max(0, min(100, score))",
     "it.trust = score", "remove trust-score clamp"),
    ("pipeline.py", "MATCH_THRESHOLD = 0.80",
     "MATCH_THRESHOLD = 0.30", "loosen human-queue threshold"),
    ("pipeline.py", "if raw_name in {_norm(a) for a in ALIAS_TO_CANONICAL}:",
     "if False:", "kill alias fast path (everything hits the judge)"),
    ("pipeline.py", "key = hashlib.md5(_norm(it.raw.text_ko).encode()).hexdigest()",
     "key = hashlib.md5(_norm(it.translation.translated_en).encode()).hexdigest()",
     "dedup on translation instead of Korean source"),
    ("pipeline.py", "score -= 25", "score -= 5", "weaken red-flag penalty"),
    ("agent.py", "fn = TOOL_IMPL.get(name)", "fn = TOOL_IMPL[name]",
     "unknown tool crashes the loop"),
]

survivors = 0
for fname, old, new, label in MUTS:
    work = pathlib.Path(tempfile.mkdtemp(prefix="mut-"))
    shutil.copytree(SRC, work, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    target = work / fname
    src = target.read_text()
    assert old in src, f"mutation target missing: {label}"
    target.write_text(src.replace(old, new, 1))
    r = subprocess.run(["pytest", "-q", "--tb=no", "-p", "no:cacheprovider"],
                       cwd=work, capture_output=True, text=True)
    caught = r.returncode != 0
    survivors += 0 if caught else 1
    print(f"{'CAUGHT  ' if caught else 'SURVIVED'}: {label}")
    shutil.rmtree(work, ignore_errors=True)

print(f"\n{len(MUTS) - survivors}/{len(MUTS)} mutants caught")
sys.exit(1 if survivors else 0)
