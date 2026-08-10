"""Check that every block is documented everywhere it is advertised.

A new block is easy to ship half-documented: registered in the engine, given a
SPEC section, and forgotten in the man page (or the reverse). Run via
``make audit``; exits non-zero when the three lists disagree.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lemontage.engine.blocks import REGISTRY  # noqa: E402
from lemontage.spec import BUILTIN_BLOCKS, RESERVED_BLOCKS  # noqa: E402

spec_text = (ROOT / "docs" / "SPEC.md").read_text()
man_text = (ROOT / "docs" / "lemontage.1").read_text()

documented = set(re.findall(r"^### 6\.\d+ `(\w+)`", spec_text, re.M))
registered = set(REGISTRY)

problems = [
    *(f"{b}: registered but no SPEC section" for b in sorted(registered - documented)),
    *(
        f"{b}: SPEC section but not registered"
        for b in sorted(documented - registered - RESERVED_BLOCKS)
    ),
    *(
        f"{b}: registered but missing from spec.BUILTIN_BLOCKS"
        for b in sorted(registered - set(BUILTIN_BLOCKS))
    ),
    *(
        f"{b}: in spec.BUILTIN_BLOCKS but not registered"
        for b in sorted(set(BUILTIN_BLOCKS) - registered)
    ),
    *(
        f"{b}: missing from the man page block list"
        for b in sorted(registered)
        if rf"\fB{b}\fR" not in man_text
    ),
]

# The SPEC section numbers must run 6.1, 6.2, … with no gap or repeat: they are
# cross-referenced by AGENTS.md and by the block docstrings.
numbers = [int(n) for n in re.findall(r"^### 6\.(\d+) ", spec_text, re.M)]
if numbers != list(range(1, len(numbers) + 1)):
    problems.append(f"SPEC §6 sections are out of order: {numbers}")

for p in problems:
    print(f"  ✗ {p}")
if not problems:
    print(f"  ✓ {len(registered)} blocks consistent across registry, SPEC and man page")
sys.exit(1 if problems else 0)
