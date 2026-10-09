"""Today's reader (read_counter) on the evaluation sets; run with the backend venv.

python baseline.py ppocrv6-small|ppocrv6-tiny <set> ...   -> runs/baseline-<reader>.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import LOCAL, REPO, WORK  # noqa: E402

HERE = WORK
sys.path.insert(0, str(REPO / "visionstate" / "backend"))

from visionstate import readers  # noqa: E402
from visionstate.backbones import download  # noqa: E402

from items import SETS  # noqa: E402

rid = sys.argv[1]
out = HERE / "runs" / f"baseline-{rid}.json"
results = json.loads(out.read_text()) if out.exists() else {}
spec = readers.READERS[rid]
reader = readers.Reader(spec, download(spec, LOCAL / "models"))
for name in sys.argv[2:]:
    rows = []
    for key, digits, truth, region in SETS[name]():
        text, _ = reader.read_counter(region, digits)
        ok = readers.digit_count(text.text) == digits
        rows.append(
            {
                "id": key,
                "truth": truth,
                "read": text.text,
                "got": int("".join(c for c in text.text if c.isdigit())) if ok else None,
            }
        )
    results[name] = rows
    print(name, len(rows), flush=True)
out.write_text(json.dumps(results, indent=1))
