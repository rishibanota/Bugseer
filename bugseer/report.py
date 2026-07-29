"""Self-contained HTML report generation, and JSON -> dataclass rehydration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bugseer.models import FileMetrics, FileRisk, GitMetrics, RepoReport, RuleHit


# --------------------------------------------------------------------------
# Rehydrate a saved report (used by `explain` and the server cache)
# --------------------------------------------------------------------------
def report_from_dict(data: dict[str, Any]) -> RepoReport:
    files: list[FileRisk] = []
    for raw in data.get("files", []):
        metrics_raw = raw.get("metrics")
        git_raw = raw.get("git")
        metrics = None
        if metrics_raw:
            known = {k: v for k, v in metrics_raw.items()
                     if k in FileMetrics.__dataclass_fields__}
            metrics = FileMetrics(**known)
        git = None
        if git_raw:
            known = {k: v for k, v in git_raw.items()
                     if k in GitMetrics.__dataclass_fields__}
            git = GitMetrics(**known)
        hits = [
            RuleHit(**{k: v for k, v in h.items() if k in RuleHit.__dataclass_fields__})
            for h in raw.get("hits", [])
        ]
        files.append(FileRisk(
            path=raw["path"],
            language=raw.get("language", "unknown"),
            score=raw.get("score", 0.0),
            raw_score=raw.get("raw_score", 0.0),
            band=raw.get("band", "low"),
            static_score=raw.get("static_score", 0.0),
            git_score=raw.get("git_score", 0.0),
            graph_score=raw.get("graph_score", 0.0),
            ml_probability=raw.get("ml_probability"),
            ml_contributions=raw.get("ml_contributions", []),
            hits=hits,
            metrics=metrics,
            git=git,
            coverage=raw.get("coverage"),
            dependents=raw.get("dependents", []),
            dependencies=raw.get("dependencies", []),
            centrality=raw.get("centrality", 0.0),
        ))

    return RepoReport(
        root=data.get("root", "."),
        generated_at=data.get("generated_at", ""),
        files=files,
        config=data.get("config", {}),
        summary=data.get("summary", {}),
        hotspots=data.get("hotspots", []),
        git_available=data.get("git_available", False),
        ml_used=data.get("ml_used", False),
        duration_seconds=data.get("duration_seconds", 0.0),
        version=data.get("version", "0.1.0"),
    )


# --------------------------------------------------------------------------
# HTML report
# --------------------------------------------------------------------------
HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BugSeer · {root_name}</title>
<style>
:root {{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2230; --border:#30363d;
  --text:#e6edf3; --dim:#8b949e;
  --low:#3fb950; --medium:#d29922; --high:#f85149; --critical:#ff6ac1;
  --accent:#58a6ff;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--bg); color:var(--text);
  font:14px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}}
a {{ color:var(--accent); text-decoration:none; }}
header {{ padding:26px 32px; border-bottom:1px solid var(--border); background:var(--panel); }}
h1 {{ margin:0 0 4px; font-size:20px; letter-spacing:-.2px; }}
h1 span {{ color:var(--accent); }}
.sub {{ color:var(--dim); font-size:12.5px; }}
.wrap {{ max-width:1280px; margin:0 auto; padding:24px 32px 80px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:26px; }}
.card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }}
.card .v {{ font-size:24px; font-weight:650; letter-spacing:-.5px; }}
.card .l {{ color:var(--dim); font-size:11.5px; text-transform:uppercase; letter-spacing:.6px; margin-top:2px; }}
.low{{color:var(--low)}} .medium{{color:var(--medium)}} .high{{color:var(--high)}} .critical{{color:var(--critical)}}
.bar {{ height:7px; border-radius:4px; background:#21262d; overflow:hidden; }}
.bar > i {{ display:block; height:100%; border-radius:4px; }}
.bg-low{{background:var(--low)}} .bg-medium{{background:var(--medium)}}
.bg-high{{background:var(--high)}} .bg-critical{{background:var(--critical)}}
.toolbar {{ display:flex; gap:10px; align-items:center; margin-bottom:14px; flex-wrap:wrap; }}
input[type=search], select {{
  background:var(--panel2); color:var(--text); border:1px solid var(--border);
  border-radius:7px; padding:7px 11px; font-size:13px; outline:none;
}}
input[type=search] {{ flex:1; min-width:220px; }}
input[type=search]:focus, select:focus {{ border-color:var(--accent); }}
.file {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; margin-bottom:9px; overflow:hidden; }}
.fhead {{ display:grid; grid-template-columns:34px 52px 110px 1fr auto; gap:14px; align-items:center;
          padding:11px 16px; cursor:pointer; user-select:none; }}
.fhead:hover {{ background:var(--panel2); }}
.fpath {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; overflow-wrap:anywhere; }}
.score {{ font-weight:700; font-size:16px; text-align:right; font-variant-numeric:tabular-nums; }}
.tags {{ color:var(--dim); font-size:11.5px; }}
.body {{ display:none; padding:4px 16px 18px 60px; border-top:1px solid var(--border); }}
.file.open .body {{ display:block; }}
.hit {{ padding:11px 0; border-bottom:1px dashed #23282f; }}
.hit:last-child {{ border-bottom:none; }}
.hit .t {{ font-weight:620; }}
.hit .pts {{ font-weight:700; margin-right:8px; font-variant-numeric:tabular-nums; }}
.hit .d {{ color:var(--dim); margin-top:3px; font-size:13px; }}
.hit .loc {{ color:var(--accent); font-family:ui-monospace,monospace; font-size:11.5px; margin-top:4px; }}
.chip {{ display:inline-block; font-size:10px; text-transform:uppercase; letter-spacing:.6px;
         padding:1.5px 7px; border-radius:20px; border:1px solid var(--border); color:var(--dim); margin-left:7px; }}
.chip.static{{border-color:#1f6feb55;color:#79c0ff}} .chip.git{{border-color:#8957e555;color:#d2a8ff}}
.chip.ml{{border-color:#db61a255;color:#ff9bce}} .chip.graph{{border-color:#3fb95055;color:#7ee787}}
.ml {{ background:var(--panel2); border-radius:8px; padding:11px 13px; margin-top:12px; }}
.ml .row {{ display:flex; justify-content:space-between; padding:3px 0; font-size:12.5px; }}
h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:.8px; color:var(--dim);
      margin:30px 0 12px; font-weight:600; }}
.note {{ background:#1c2230; border:1px solid var(--border); border-left:3px solid var(--accent);
         border-radius:7px; padding:12px 15px; color:var(--dim); font-size:12.5px; margin-bottom:22px; }}
footer {{ color:var(--dim); font-size:11.5px; text-align:center; padding:26px; border-top:1px solid var(--border); }}
.tree {{ font-family:ui-monospace,monospace; font-size:13px; line-height:1.85; }}
.tree .d {{ color:var(--dim); }}
.empty {{ color:var(--dim); padding:26px; text-align:center; }}
</style>
</head>
<body>
<header>
  <h1>Bug<span>Seer</span> · {root_name}</h1>
  <div class="sub">
    {files_scanned} files · {total_loc} LOC · generated {generated_at} · v{version}
    · <strong>computed entirely offline</strong>
  </div>
</header>

<div class="wrap">
  <div class="cards">{cards}</div>

  <div class="note">{provenance}</div>

  <h2>Risk heat map</h2>
  <div class="tree">{tree}</div>

  <h2>Files by risk</h2>
  <div class="toolbar">
    <input type="search" id="q" placeholder="Filter by path, rule or reason…">
    <select id="bandf">
      <option value="">All bands</option>
      <option value="critical">Critical only</option>
      <option value="high">High and above</option>
      <option value="medium">Medium and above</option>
    </select>
    <span class="tags" id="count"></span>
  </div>
  <div id="files">{files}</div>
</div>

<footer>
  BugSeer {version} · every score above is traceable to the listed evidence ·
  no code or metrics were uploaded anywhere
</footer>

<script>
const cards = document.querySelectorAll('.fhead');
cards.forEach(h => h.addEventListener('click', () => h.parentElement.classList.toggle('open')));

const q = document.getElementById('q');
const bandf = document.getElementById('bandf');
const counter = document.getElementById('count');
const order = {{critical:3, high:2, medium:1, low:0}};

function apply() {{
  const term = q.value.toLowerCase();
  const minBand = bandf.value ? order[bandf.value] : -1;
  let shown = 0;
  document.querySelectorAll('.file').forEach(el => {{
    const hay = el.dataset.search;
    const band = order[el.dataset.band];
    const ok = (!term || hay.includes(term)) && band >= minBand;
    el.style.display = ok ? '' : 'none';
    if (ok) shown++;
  }});
  counter.textContent = shown + ' file' + (shown === 1 ? '' : 's') + ' shown';
}}
q.addEventListener('input', apply);
bandf.addEventListener('change', apply);
apply();
</script>
</body>
</html>"""


def _esc(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_cards(report: RepoReport) -> str:
    s = report.summary
    bands = s.get("bands", {})
    git = s.get("git") or {}
    ml = s.get("ml") or {}

    cards = [
        ("critical", bands.get("critical", 0), "Critical"),
        ("high", bands.get("high", 0), "High risk"),
        ("medium", bands.get("medium", 0), "Medium"),
        ("low", bands.get("low", 0), "Low"),
    ]
    html = "".join(
        f'<div class="card"><div class="v {cls}">{value}</div><div class="l">{label}</div></div>'
        for cls, value, label in cards
    )
    html += (
        f'<div class="card"><div class="v">{s.get("average_score", 0)}</div>'
        f'<div class="l">Average score</div></div>'
    )
    if git.get("available"):
        html += (
            f'<div class="card"><div class="v">{git.get("bugfix_ratio", 0):.0%}</div>'
            f'<div class="l">Commits that are fixes</div></div>'
        )
    if ml.get("trained") and ml.get("auc"):
        html += (
            f'<div class="card"><div class="v">{ml["auc"]}</div>'
            f'<div class="l">Model AUC</div></div>'
        )
    return html


def _render_provenance(report: RepoReport) -> str:
    s = report.summary
    parts = [
        "<strong>How these scores were produced.</strong> "
        f"Phase 1 parsed {s.get('files_scanned', 0)} files "
        f"({', '.join(f'{k} ×{v}' for k, v in (s.get('parsers') or {}).items())})."
    ]
    git = s.get("git") or {}
    if git.get("available"):
        parts.append(
            f"Phase 2 replayed {git.get('commits_analyzed', 0)} commits and identified "
            f"{git.get('bugfix_commits', 0)} bug fixes and {git.get('reverts', 0)} reverts."
        )
    else:
        parts.append("Phase 2 was skipped (not a git repository).")

    ml = s.get("ml") or {}
    if ml.get("trained"):
        parts.append(
            f"Phase 3 trained {ml.get('estimator', 'a model')} locally on "
            f"{ml.get('samples', 0)} files, of which {ml.get('positives', 0)} were "
            f"bug-fixed in the last {ml.get('label_window_days', 0)} days"
            + (f" (out-of-fold AUC {ml['auc']})." if ml.get("auc") else ".")
        )
    elif report.ml_used:
        parts.append("Phase 3 used a previously trained local model.")
    else:
        parts.append(
            "Phase 3 is inactive — run <code>bugseer train</code> to learn from this "
            "repository's own bug history."
        )

    graph = s.get("graph", {})
    parts.append(
        f"Phase 5 built a graph of {graph.get('import_edges', 0)} import edges and "
        f"{graph.get('cochange_edges', 0)} co-change edges."
    )
    return " ".join(parts)


def _render_tree(report: RepoReport, limit: int = 60) -> str:
    by_dir: dict[str, list[FileRisk]] = {}
    for f in report.files:
        directory = "/".join(f.path.split("/")[:-1]) or "."
        by_dir.setdefault(directory, []).append(f)

    lines: list[str] = []
    shown = 0
    for directory in sorted(by_dir):
        files = sorted(by_dir[directory], key=lambda x: x.score, reverse=True)
        worst = max(f.score for f in files)
        band = ("low" if worst < 35 else "medium" if worst < 60
                else "high" if worst < 85 else "critical")
        lines.append(
            f'<div><strong>{_esc(directory)}/</strong> '
            f'<span class="d">{len(files)} files · worst </span>'
            f'<span class="{band}">{worst:.0f}</span></div>'
        )
        for f in files[:12]:
            if shown >= limit:
                break
            emoji = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🔥"}[f.band]
            name = f.path.split("/")[-1]
            reasons = " · ".join(h.title for h in f.top_reasons(2))
            lines.append(
                f'<div>&nbsp;&nbsp;{emoji} <a href="#f-{abs(hash(f.path))}">{_esc(name)}</a> '
                f'<span class="{f.band}">{f.score:.0f}</span> '
                f'<span class="d">{_esc(reasons)}</span></div>'
            )
            shown += 1
        if shown >= limit:
            lines.append('<div class="d">…truncated</div>')
            break
    return "\n".join(lines)


def _render_file(f: FileRisk) -> str:
    hits_html: list[str] = []
    for hit in sorted(f.hits, key=lambda h: h.score, reverse=True):
        locations = ""
        if hit.locations:
            locs = ", ".join(
                f"L{loc.get('line')}"
                + (f" {_esc(loc.get('name'))}" if loc.get("name") else "")
                + (f" ({_esc(loc.get('note'))})" if loc.get("note") else "")
                for loc in hit.locations[:6]
            )
            locations = f'<div class="loc">↳ {locs}</div>'
        hits_html.append(
            f'<div class="hit">'
            f'<div><span class="pts {f.band}">+{hit.score:.0f}</span>'
            f'<span class="t">{_esc(hit.title)}</span>'
            f'<span class="chip {hit.phase}">{hit.phase}</span>'
            f'<span class="chip">{_esc(hit.rule_id)}</span></div>'
            f'<div class="d">{_esc(hit.detail)}</div>{locations}</div>'
        )

    ml_html = ""
    if f.ml_contributions:
        rows = "".join(
            f'<div class="row"><span>{"▲" if c["direction"] == "increases" else "▼"} '
            f'{_esc(c["label"])}</span>'
            f'<span><strong>{c["value"]}</strong> '
            f'<span class="d">({c["z_score"]:+.1f}σ)</span></span></div>'
            for c in f.ml_contributions[:5]
        )
        prob = f"{f.ml_probability:.0%}" if f.ml_probability is not None else "—"
        ml_html = (
            f'<div class="ml"><div class="row"><strong>Local model: {prob} '
            f'probability of needing a fix</strong></div>{rows}</div>'
        )

    coupling = ""
    if f.dependents:
        coupling += (
            f'<div class="d" style="margin-top:10px">⇄ {len(f.dependents)} file(s) '
            f'import this: {_esc(", ".join(f.dependents[:6]))}</div>'
        )
    if f.git and f.git.co_change_partners:
        partners = ", ".join(
            f"{p['path']} ({p['strength']:.0%})" for p in f.git.co_change_partners[:4]
        )
        coupling += f'<div class="d">⎇ usually changes with: {_esc(partners)}</div>'

    search_blob = _esc(
        (f.path + " " + " ".join(h.rule_id + " " + h.title for h in f.hits)).lower()
    )
    emoji = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🔥"}[f.band]
    tags = " · ".join(h.title for h in f.top_reasons(3)) or "no rules triggered"

    return (
        f'<div class="file" id="f-{abs(hash(f.path))}" data-band="{f.band}" '
        f'data-search="{search_blob}">'
        f'<div class="fhead">'
        f'<div>{emoji}</div>'
        f'<div class="score {f.band}">{f.score:.0f}</div>'
        f'<div class="bar"><i class="bg-{f.band}" style="width:{f.score:.0f}%"></i></div>'
        f'<div class="fpath">{_esc(f.path)}</div>'
        f'<div class="tags">{_esc(tags)}</div>'
        f'</div>'
        f'<div class="body">{"".join(hits_html) or "<div class=d>No rules triggered.</div>"}'
        f'{ml_html}{coupling}</div></div>'
    )


def write_html_report(report: RepoReport, out_path: Path,
                      graph_json: dict[str, Any] | None = None) -> Path:
    out_path = Path(out_path)
    s = report.summary
    html = HTML_TEMPLATE.format(
        root_name=_esc(Path(report.root).name or report.root),
        files_scanned=s.get("files_scanned", 0),
        total_loc=f"{s.get('total_loc', 0):,}",
        generated_at=_esc(report.generated_at),
        version=_esc(report.version),
        cards=_render_cards(report),
        provenance=_render_provenance(report),
        tree=_render_tree(report),
        files="".join(_render_file(f) for f in report.files[:300]),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
