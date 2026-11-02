"""Builds the public demo website: the real dashboard plus exported demo data.

    python site/build.py _site
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from importlib import resources
from pathlib import Path

from freezeframe.demo import build_demo
from freezeframe.server import Api

HERE = Path(__file__).parent


def compact(value):
    """Rounds floats so the data file stays small."""
    if isinstance(value, float):
        return round(value, 2) if abs(value) < 1e4 else round(value)
    if isinstance(value, list):
        return [compact(v) for v in value]
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    return value


def export(db: Path) -> dict:
    api = Api(db, platform="darwin")
    overview = api.overview()
    overview.update(site=True, live=False)
    first, last = overview["first"], overview["last"]
    timeline = api.timeline(first, last, 2400)
    incidents = {e["id"]: api.diagnose({"id": str(e["id"])}) for e in overview["incidents"]}
    sampled = [p["t"] for p in timeline["series"]]
    moments = []
    t = first + 60
    while t <= last:
        if any(abs(s - t) <= 60 for s in sampled[:: max(1, len(sampled) // 400)]):
            moments.append({"at": t, "diagnosis": api.diagnose({"at": str(t)})["diagnosis"]})
        t += 60
    return compact({"overview": overview, "timeline": timeline, "incidents": incidents, "moments": moments, "now": api.now()})


def main(out: str) -> None:
    out_dir = Path(out)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        data = export(build_demo(Path(tmp) / "demo.db", end=time.time()))
    web = resources.files("freezeframe") / "web"
    for name in ("app.js", "style.css", "favicon.svg"):
        (out_dir / name).write_bytes((web / name).read_bytes())
    html = (web / "index.html").read_text()
    html = html.replace("<title>Freezeframe</title>", "<title>Freezeframe: why did my computer freeze?</title>")
    html = html.replace(
        "    <!-- static-demo -->",
        '    <script src="demo-data.js"></script>\n    <script src="static.js"></script>',
    )
    (out_dir / "index.html").write_text(html)
    (out_dir / "static.js").write_text((HERE / "static.js").read_text())
    (out_dir / "demo-data.js").write_text("window.FREEZEFRAME_DEMO = " + json.dumps(data, separators=(",", ":")) + ";\n")
    size = sum(p.stat().st_size for p in out_dir.iterdir())
    print(f"Built {out_dir} ({size / 1e6:.1f} MB, {len(data['moments'])} explainable moments)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "_site")
