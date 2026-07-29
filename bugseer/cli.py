"""BugSeer command-line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn,
)
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from bugseer import __version__
from bugseer.config import load_config
from bugseer.models import FileRisk, RepoReport
from bugseer.scanner import Scanner, save_report

app = typer.Typer(
    name="bugseer",
    help=(
        "Offline bug-risk prediction. Static analysis + git intelligence + a local "
        "ML model. No cloud, no API keys, your code never leaves your machine."
    ),
    add_completion=False,
    no_args_is_help=True,
)
console = Console()

BAND_STYLE = {
    "low": "green",
    "medium": "yellow",
    "high": "red",
    "critical": "bold red",
}
BAND_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🔥"}


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _risk_bar(score: float, width: int = 20) -> Text:
    filled = int(round(score / 100 * width))
    band = "low" if score < 35 else "medium" if score < 60 else "high" if score < 85 else "critical"
    bar = Text()
    bar.append("█" * filled, style=BAND_STYLE[band])
    bar.append("░" * (width - filled), style="dim")
    return bar


def _run_scan(root: Path, *, train: bool = False, use_model: bool = True,
              quiet: bool = False, **overrides) -> tuple[RepoReport, Scanner]:
    cfg = load_config(root, overrides)
    if not quiet:
        console.print(
            f"[dim]Scanning[/dim] [cyan]{cfg.root}[/cyan] "
            f"[dim](offline - nothing is uploaded)[/dim]"
        )

    if quiet:
        scanner = Scanner(cfg)
        return scanner.scan(train=train, use_model=use_model), scanner

    labels = {
        "static": "Analysing source",
        "git": "Reading git history",
        "graph": "Building dependency graph",
        "train": "Training local model",
        "score": "Scoring files",
    }
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        tasks: dict[str, int] = {}

        def on_progress(stage: str, done: int, total: int) -> None:
            if stage not in tasks:
                tasks[stage] = progress.add_task(labels.get(stage, stage), total=max(1, total))
            progress.update(tasks[stage], completed=done, total=max(1, total))

        scanner = Scanner(cfg, progress=on_progress)
        report = scanner.scan(train=train, use_model=use_model)
    return report, scanner


def _print_summary(report: RepoReport) -> None:
    s = report.summary
    bands = s["bands"]
    parts = [
        f"[bold]{s['files_scanned']}[/bold] files",
        f"[bold]{s['total_loc']:,}[/bold] LOC",
        f"avg risk [bold]{s['average_score']}[/bold]",
    ]
    band_text = "  ".join(
        f"{BAND_EMOJI[b]} [{BAND_STYLE[b]}]{bands[b]}[/{BAND_STYLE[b]}] {b}"
        for b in ("critical", "high", "medium", "low") if bands[b]
    )
    lines = ["  ".join(parts), band_text]

    git = s.get("git") or {}
    if git.get("available"):
        lines.append(
            f"[dim]git:[/dim] {git['commits_analyzed']} commits, "
            f"{git['bugfix_commits']} bug fixes ({git['bugfix_ratio']:.0%}), "
            f"{git['reverts']} reverts, {git['contributors']} contributors"
        )
        if git.get("degraded"):
            lines.append(f"[yellow]  ⚠ {git.get('degraded_reason')}[/yellow]")
    else:
        reason = git.get("reason") or "not a repository"
        lines.append(f"[dim]git: {reason} - Phase 2 signals unavailable[/dim]")

    ml = s.get("ml")
    if ml and ml.get("trained"):
        auc = f", AUC {ml['auc']}" if ml.get("auc") else ""
        lines.append(
            f"[dim]model:[/dim] {ml['estimator'].split('.')[-1]} on {ml['samples']} "
            f"samples ({ml['positives']} bug-fixed){auc}"
        )
    elif report.ml_used:
        lines.append("[dim]model:[/dim] loaded from cache")

    graph = s.get("graph", {})
    lines.append(
        f"[dim]graph:[/dim] {graph.get('import_edges', 0)} import edges, "
        f"{graph.get('cochange_edges', 0)} co-change edges"
    )
    parsers = ", ".join(f"{k}×{v}" for k, v in (s.get("parsers") or {}).items())
    lines.append(f"[dim]parsers:[/dim] {parsers}  [dim]in {report.duration_seconds:.2f}s[/dim]")

    console.print(Panel("\n".join(lines), title="BugSeer", border_style="cyan", expand=False))


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------
@app.command()
def scan(
    path: Path = typer.Argument(Path("."), help="Repository or directory to analyse."),
    top: int = typer.Option(20, "--top", "-n", help="How many files to list."),
    min_score: float = typer.Option(0.0, "--min-score", help="Only show files at or above this score."),
    band: Optional[str] = typer.Option(None, "--band", help="Filter: low|medium|high|critical."),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write the full report as JSON."),
    html_out: Optional[Path] = typer.Option(None, "--html", help="Write a self-contained HTML report."),
    no_git: bool = typer.Option(False, "--no-git", help="Skip git history analysis."),
    no_ml: bool = typer.Option(False, "--no-ml", help="Skip the learned model."),
    ignore_tests: bool = typer.Option(False, "--ignore-tests", help="Exclude test files."),
    exclude: list[str] = typer.Option([], "--exclude", "-e", help="Extra glob to exclude (repeatable)."),
    history_days: Optional[int] = typer.Option(None, "--history-days", help="Days of git history (0 = all)."),
    fail_over: Optional[float] = typer.Option(
        None, "--fail-over", help="Exit non-zero if any file scores above this (for CI)."
    ),
    detail: bool = typer.Option(False, "--detail", "-d", help="Show the reasons under each file."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress progress output."),
) -> None:
    """Scan a repository and rank files by bug risk."""
    report, scanner = _run_scan(
        path, use_model=not no_ml, quiet=quiet,
        use_git=not no_git, use_ml=not no_ml, ignore_tests=ignore_tests,
        exclude=list(exclude), history_days=history_days,
    )

    if not quiet:
        _print_summary(report)

    files = [f for f in report.files if f.score >= min_score]
    if band:
        files = [f for f in files if f.band == band.lower()]
    shown = files[:top]

    if not shown:
        console.print("[green]No files matched the filter. Nothing to worry about here.[/green]")
    else:
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("", width=2)
        table.add_column("Risk", justify="right", width=6)
        table.add_column("", width=20)
        table.add_column("File", overflow="fold")
        table.add_column("Top reasons", overflow="fold")

        for f in shown:
            reasons = " · ".join(h.title for h in f.top_reasons(3)) or "—"
            table.add_row(
                BAND_EMOJI[f.band],
                Text(f"{f.score:.0f}", style=BAND_STYLE[f.band]),
                _risk_bar(f.score),
                Text(f.path, style="bold" if f.band in ("high", "critical") else ""),
                Text(reasons, style="dim"),
            )
        console.print(table)

        if detail:
            for f in shown:
                console.print()
                _print_file_detail(f, show_source=False)

    if len(files) > top:
        console.print(f"[dim]… and {len(files) - top} more. Use --top to see them.[/dim]")

    if json_out:
        save_report(report, json_out)
        console.print(f"[green]✓[/green] JSON report → [cyan]{json_out}[/cyan]")

    if html_out:
        from bugseer.report import write_html_report
        graph_json = scanner.graph_json({f.path: f.score for f in report.files})
        write_html_report(report, html_out, graph_json)
        console.print(f"[green]✓[/green] HTML report → [cyan]{html_out}[/cyan]")

    # Always cache the latest scan so `serve`/`impact`/`explain` are instant.
    cache = load_config(path).home_path / "last-scan.json"
    try:
        save_report(report, cache)
    except OSError:
        pass

    if fail_over is not None:
        over = [f for f in report.files if f.score > fail_over]
        if over:
            console.print(
                f"\n[bold red]FAIL[/bold red] {len(over)} file(s) exceed the risk "
                f"threshold of {fail_over}:"
            )
            for f in over[:10]:
                console.print(f"  {BAND_EMOJI[f.band]} {f.score:.0f}  {f.path}")
            raise typer.Exit(code=1)
        console.print(f"\n[green]PASS[/green] no file exceeds risk {fail_over}.")


# --------------------------------------------------------------------------
# explain
# --------------------------------------------------------------------------
def _print_file_detail(f: FileRisk, *, show_source: bool = True, root: Path | None = None) -> None:
    header = Text()
    header.append(f"{BAND_EMOJI[f.band]} ", style="")
    header.append(f.path, style="bold")
    header.append(f"   {f.score:.0f}/100 ", style=BAND_STYLE[f.band])
    header.append(f"({f.band})", style="dim")
    console.print(header)
    console.print(_risk_bar(f.score, 40))

    breakdown = []
    if f.static_score:
        breakdown.append(f"static {f.static_score:.0f}")
    if f.git_score:
        breakdown.append(f"git {f.git_score:.0f}")
    if f.graph_score:
        breakdown.append(f"graph {f.graph_score:.0f}")
    if f.ml_probability is not None:
        breakdown.append(f"model {f.ml_probability:.0%}")
    if breakdown:
        console.print(f"[dim]{'  ·  '.join(breakdown)}  ·  raw {f.raw_score:.0f} pts[/dim]")

    if not f.hits:
        console.print("[green]  No rules triggered.[/green]")
        return

    console.print()
    for hit in sorted(f.hits, key=lambda h: h.score, reverse=True):
        marker = {"static": "◆", "git": "⎇", "ml": "◈", "graph": "⇄"}.get(hit.phase, "•")
        style = {"high": "red", "medium": "yellow", "low": "dim"}.get(hit.severity, "white")
        line = Text("  ")
        line.append(f"{marker} ", style=style)
        line.append(f"+{hit.score:.0f} ", style=f"bold {style}")
        line.append(hit.title, style="bold")
        line.append(f"  [{hit.rule_id}]", style="dim")
        console.print(line)
        console.print(Text(f"      {hit.detail}", style="dim"), width=100)
        if hit.locations:
            locs = ", ".join(
                f"L{loc.get('line')}" + (f" {loc.get('name')}" if loc.get("name") else "")
                + (f" ({loc.get('note')})" if loc.get("note") else "")
                for loc in hit.locations[:5]
            )
            console.print(Text(f"      ↳ {locs}", style="cyan dim"))
        console.print()

    if f.ml_contributions:
        console.print("  [bold]Model reasoning[/bold] [dim](local, trained on this repo)[/dim]")
        for c in f.ml_contributions[:5]:
            arrow = "▲" if c["direction"] == "increases" else "▼"
            colour = "red" if c["direction"] == "increases" else "green"
            console.print(
                f"      [{colour}]{arrow}[/{colour}] {c['label']}: [bold]{c['value']}[/bold] "
                f"[dim]({c['z_score']:+.1f}σ vs repo average)[/dim]"
            )
        console.print()

    if f.dependents:
        console.print(
            f"  [dim]⇄ {len(f.dependents)} file(s) import this: "
            f"{', '.join(f.dependents[:5])}"
            + ("…" if len(f.dependents) > 5 else "") + "[/dim]"
        )
    if f.git and f.git.co_change_partners:
        partners = ", ".join(
            f"{p['path']} ({p['strength']:.0%})" for p in f.git.co_change_partners[:4]
        )
        console.print(f"  [dim]⎇ usually changes alongside: {partners}[/dim]")


@app.command()
def explain(
    file: str = typer.Argument(..., help="Path of the file to explain."),
    path: Path = typer.Option(Path("."), "--repo", "-r", help="Repository root."),
    narrate_it: bool = typer.Option(
        False, "--narrate", help="Also produce a prose summary via an optional AI provider."
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", help="openai | anthropic | gemini | ollama (overrides .env)."
    ),
    no_cache: bool = typer.Option(False, "--fresh", help="Re-scan instead of using the cached scan."),
) -> None:
    """Explain in full why one file is considered risky."""
    cfg = load_config(path)
    cache = cfg.home_path / "last-scan.json"

    report: RepoReport | None = None
    if not no_cache and cache.is_file():
        try:
            from bugseer.report import report_from_dict
            report = report_from_dict(json.loads(cache.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            report = None
    if report is None:
        report, _ = _run_scan(path, quiet=False)

    target = report.by_path(file)
    if target is None:
        candidates = [f for f in report.files if f.path.endswith(file) or file in f.path]
        if len(candidates) == 1:
            target = candidates[0]
        elif candidates:
            console.print(f"[yellow]Ambiguous:[/yellow] '{file}' matches several files:")
            for c in candidates[:10]:
                console.print(f"  {c.path}")
            raise typer.Exit(code=2)
        else:
            console.print(f"[red]Not found:[/red] '{file}' is not in the scan.")
            raise typer.Exit(code=2)

    console.print()
    _print_file_detail(target, root=cfg.root)

    if narrate_it:
        from bugseer.narrate import narrate as do_narrate
        console.print()
        with console.status("Asking the configured AI provider to summarise…"):
            source_text = None
            if cfg.ai_send_source:
                try:
                    source_text = (cfg.root / target.path).read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    pass
            result = do_narrate(target, provider=provider, source_text=source_text)
        if result.ok:
            console.print(Panel(
                result.text,
                title=f"AI summary ({result.provider})",
                subtitle="[dim]narration only - the score above is computed locally[/dim]",
                border_style="magenta",
            ))
        else:
            console.print(Panel(
                result.error,
                title="AI narration unavailable",
                subtitle="[dim]the deterministic analysis above is unaffected[/dim]",
                border_style="yellow",
            ))


# --------------------------------------------------------------------------
# heatmap
# --------------------------------------------------------------------------
@app.command()
def heatmap(
    path: Path = typer.Argument(Path("."), help="Repository to analyse."),
    depth: int = typer.Option(4, "--depth", help="Maximum directory depth to display."),
    min_score: float = typer.Option(0.0, "--min-score", help="Hide files below this score."),
    no_git: bool = typer.Option(False, "--no-git"),
    no_ml: bool = typer.Option(False, "--no-ml"),
) -> None:
    """Render the project as a colour-coded risk tree (Phase 4, terminal edition)."""
    report, _ = _run_scan(path, use_model=not no_ml, use_git=not no_git, use_ml=not no_ml)
    _print_summary(report)

    files = [f for f in report.files if f.score >= min_score]
    by_dir: dict[str, list[FileRisk]] = {}
    for f in files:
        parts = f.path.split("/")
        key = "/".join(parts[:-1][:depth]) or "."
        by_dir.setdefault(key, []).append(f)

    root_label = Text(str(report.root).split("/")[-1] or "/", style="bold cyan")
    tree = Tree(root_label)
    nodes: dict[str, Tree] = {"": tree}

    for directory in sorted(by_dir):
        current = tree
        accumulated = ""
        if directory != ".":
            for part in directory.split("/"):
                accumulated = f"{accumulated}/{part}" if accumulated else part
                if accumulated not in nodes:
                    dir_files = [
                        f for f in files if f.path.startswith(f"{accumulated}/")
                    ]
                    worst = max((f.score for f in dir_files), default=0.0)
                    dir_band = (
                        "low" if worst < 35 else "medium" if worst < 60
                        else "high" if worst < 85 else "critical"
                    )
                    label = Text()
                    label.append(f"{part}/", style="bold")
                    label.append(f"  {len(dir_files)} files", style="dim")
                    label.append(f"  worst {worst:.0f}", style=BAND_STYLE[dir_band])
                    nodes[accumulated] = current.add(label)
                current = nodes[accumulated]

        for f in sorted(by_dir[directory], key=lambda x: x.score, reverse=True):
            name = f.path.split("/")[-1]
            label = Text()
            label.append(f"{BAND_EMOJI[f.band]} ")
            label.append(name, style=BAND_STYLE[f.band] if f.band != "low" else "")
            label.append(f"  {f.score:.0f}", style=f"dim {BAND_STYLE[f.band]}")
            top = f.top_reasons(2)
            if top and f.band in ("high", "critical", "medium"):
                label.append(f"  · {' · '.join(h.title for h in top)}", style="dim")
            current.add(label)

    console.print(tree)
    console.print(
        "\n[dim]Run [/dim][cyan]bugseer explain <file>[/cyan][dim] for the full reasoning "
        "behind any file.[/dim]"
    )


# --------------------------------------------------------------------------
# impact  (Phase 5)
# --------------------------------------------------------------------------
@app.command()
def impact(
    files: list[str] = typer.Argument(..., help="File(s) you intend to change."),
    path: Path = typer.Option(Path("."), "--repo", "-r", help="Repository root."),
    hops: int = typer.Option(3, "--hops", help="Maximum dependency hops to traverse."),
    limit: int = typer.Option(15, "--limit", "-n", help="How many affected files to show."),
) -> None:
    """"What if?" - predict the blast radius of changing one or more files."""
    from bugseer.graph import simulate_impact

    report, scanner = _run_scan(path, quiet=False)
    risk_by_path = {f.path: f.score for f in report.files}

    resolved: list[str] = []
    for wanted in files:
        if wanted in risk_by_path:
            resolved.append(wanted)
            continue
        matches = [p for p in risk_by_path if p.endswith(wanted) or wanted in p]
        if len(matches) == 1:
            resolved.append(matches[0])
        elif matches:
            console.print(f"[yellow]Ambiguous '{wanted}':[/yellow] {', '.join(matches[:6])}")
            raise typer.Exit(code=2)
        else:
            console.print(f"[red]Not found:[/red] {wanted}")
            raise typer.Exit(code=2)

    result = simulate_impact(
        scanner.graph, resolved,
        risk_by_path=risk_by_path,
        cochange_strength=scanner.cochange_strength(),
        max_hops=hops, limit=limit,
    )

    console.print()
    console.print(Panel(
        Text.from_markup(
            f"If you modify [bold cyan]{', '.join(resolved)}[/bold cyan], "
            f"[bold]{len(result.affected)}[/bold] file(s) are most likely to be affected."
        ),
        border_style="cyan", expand=False,
    ))

    if not result.affected:
        console.print("[green]No coupled files detected. This change looks well isolated.[/green]")
    else:
        table = Table(show_header=True, header_style="bold", box=None)
        table.add_column("Impact", justify="right", width=6)
        table.add_column("", width=14)
        table.add_column("File", overflow="fold")
        table.add_column("Own risk", justify="right", width=8)
        table.add_column("Why", overflow="fold")

        for item in result.affected:
            impact_band = (
                "critical" if item["impact_score"] >= 70 else
                "high" if item["impact_score"] >= 45 else
                "medium" if item["impact_score"] >= 25 else "low"
            )
            table.add_row(
                Text(f"{item['impact_score']:.0f}", style=BAND_STYLE[impact_band]),
                _risk_bar(item["impact_score"], 12),
                item["path"],
                Text(f"{item['own_risk']:.0f}",
                     style=BAND_STYLE["high" if item["own_risk"] >= 60 else "low"]),
                Text("; ".join(item["reasons"][:2]), style="dim"),
            )
        console.print(table)

    console.print()
    for line in result.explanation:
        console.print(f"[dim]· {line}[/dim]")


# --------------------------------------------------------------------------
# train  (Phase 3)
# --------------------------------------------------------------------------
@app.command()
def train(
    path: Path = typer.Argument(Path("."), help="Repository to learn from."),
    label_window: int = typer.Option(
        180, "--label-window", help="Days of recent history used as bug labels."
    ),
    history_days: int = typer.Option(
        1460, "--history-days", help="Total days of history to read."
    ),
) -> None:
    """Train a local model on this repository's own bug-fix history."""
    console.print(
        Panel(
            "Training runs entirely on your machine.\n"
            "[dim]Labels come from bug-fix commits in the recent window; features come "
            "from the history before it, so the model is genuinely predictive.[/dim]",
            title="Phase 3 · local learning", border_style="cyan", expand=False,
        )
    )

    cfg = load_config(path, {"history_days": history_days})
    from bugseer.git_intel import is_git_repo
    if not is_git_repo(cfg.root):
        console.print("[red]Not a git repository[/red] - there is no history to learn from.")
        raise typer.Exit(code=1)

    from bugseer.ml import BugPredictor
    from bugseer.scanner import discover_files
    from bugseer.analysis.static import analyze_file
    from bugseer.graph import build_dependency_graph

    with console.status("Analysing source files…"):
        files = discover_files(cfg)
        metrics_by_path = {}
        for abs_path, rel_path, language in files:
            metrics, _ = analyze_file(abs_path, rel_path, language, cfg.max_file_bytes)
            metrics_by_path[rel_path] = metrics
        graph = build_dependency_graph(metrics_by_path)
        degrees = {p: (graph.out_degree(p), graph.in_degree(p)) for p in metrics_by_path}

    predictor = BugPredictor(cfg.home_path)
    with console.status("Replaying git history and fitting the model…"):
        report = predictor.train(
            cfg.root, metrics_by_path, degrees,
            label_window_days=label_window, history_days=history_days,
        )

    if not report.trained:
        console.print(Panel(report.reason, title="Not enough signal", border_style="yellow"))
        console.print(
            "[dim]BugSeer will continue using rule-based scoring, which needs no "
            "training data.[/dim]"
        )
        raise typer.Exit(code=0)

    predictor.save()
    table = Table(show_header=False, box=None)
    table.add_row("Estimator", report.estimator)
    table.add_row("Samples", f"{report.samples} files ({report.positives} bug-fixed)")
    table.add_row("Baseline rate", f"{(report.baseline_rate or 0):.1%}")
    if report.auc is not None:
        table.add_row("ROC AUC (5-fold)", f"{report.auc}")
        table.add_row("Precision / Recall", f"{report.precision} / {report.recall}")
    table.add_row("Label window", f"{report.label_window_days} days")
    table.add_row("Saved to", str(predictor.model_path))
    console.print(Panel(table, title="✓ Model trained", border_style="green", expand=False))

    if report.top_features:
        console.print("\n[bold]What the model learned to look at:[/bold]")
        for feature in report.top_features[:8]:
            width = int(feature["importance"] * 40)
            console.print(
                f"  {feature['label']:32s} [cyan]{'█' * width}[/cyan] "
                f"[dim]{feature['importance']:.1%}[/dim]"
            )


# --------------------------------------------------------------------------
# serve  (Phase 4)
# --------------------------------------------------------------------------
@app.command()
def serve(
    path: Path = typer.Argument(Path("."), help="Repository to serve."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8420, "--port"),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser."),
) -> None:
    """Launch the local dashboard (heat map, graph, what-if simulator)."""
    try:
        import uvicorn  # noqa: F401
    except ModuleNotFoundError:
        console.print(
            "[red]The dashboard needs FastAPI and uvicorn.[/red]\n"
            r"  [cyan]pip install 'bugseer\[server]'[/cyan]"
        )
        raise typer.Exit(code=1)

    from bugseer.server import run_server
    console.print(
        Panel(
            f"Dashboard: [bold cyan]http://{host}:{port}[/bold cyan]\n"
            f"[dim]Serving {Path(path).resolve()} — bound to localhost, nothing leaves "
            f"your machine.[/dim]",
            border_style="cyan", expand=False,
        )
    )
    run_server(Path(path).resolve(), host=host, port=port, open_browser=not no_open)


# --------------------------------------------------------------------------
# report / init / version
# --------------------------------------------------------------------------
@app.command()
def report(
    path: Path = typer.Argument(Path("."), help="Repository to analyse."),
    out: Path = typer.Option(Path("bugseer-report.html"), "--out", "-o", help="Output HTML file."),
    no_git: bool = typer.Option(False, "--no-git"),
    no_ml: bool = typer.Option(False, "--no-ml"),
) -> None:
    """Generate a self-contained HTML report (no server, no build step)."""
    from bugseer.report import write_html_report

    scan_report, scanner = _run_scan(path, use_model=not no_ml, use_git=not no_git, use_ml=not no_ml)
    _print_summary(scan_report)
    graph_json = scanner.graph_json({f.path: f.score for f in scan_report.files})
    write_html_report(scan_report, out, graph_json)
    console.print(f"[green]✓[/green] Report written → [cyan]{out.resolve()}[/cyan]")
    console.print("[dim]Open it in any browser; it is a single offline file.[/dim]")


@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Where to create the config."),
) -> None:
    """Write a starter .bugseer.toml you can tune."""
    target = Path(path) / ".bugseer.toml"
    if target.exists():
        console.print(f"[yellow]{target} already exists.[/yellow]")
        raise typer.Exit(code=1)

    target.write_text(
        """# BugSeer configuration. Every value is optional.
[bugseer]
# Days of git history to analyse (0 = everything).
history_days = 730
# Exclude extra paths (these extend the built-in defaults).
exclude = ["docs/*", "examples/*"]
# Skip test files entirely when scoring.
ignore_tests = false
# Point at a coverage report for exact coverage instead of the naming heuristic.
# coverage_file = "coverage.xml"

# Tune how many points each rule contributes.
[bugseer.weights]
no_error_handling = 20
long_function = 10
deep_nesting = 15
global_variables = 15
duplicate_code = 10
low_test_coverage = 20
change_frequency = 15
bugfix_density = 18

# Tune when each rule starts firing.
[bugseer.thresholds]
long_function_lines = 100
nested_loop_depth = 3
cyclomatic_complexity = 20
commit_count = 20
band_high = 60
band_critical = 85
""",
        encoding="utf-8",
    )
    console.print(f"[green]✓[/green] Created [cyan]{target}[/cyan]")
    console.print("[dim]Also copy .env.example → .env if you want the optional AI narrator.[/dim]")


@app.command()
def version() -> None:
    """Show version and which optional components are active."""
    from bugseer.analysis.static import tree_sitter_available

    table = Table(show_header=False, box=None)
    table.add_row("BugSeer", f"[bold]{__version__}[/bold]")
    table.add_row("Python", sys.version.split()[0])

    def mark(ok: bool, yes: str, no: str) -> str:
        return f"[green]✓[/green] {yes}" if ok else f"[dim]○ {no}[/dim]"

    table.add_row("Parsers", mark(
        tree_sitter_available(),
        "tree-sitter (multi-language)",
        r"stdlib ast + heuristics (pip install 'bugseer\[parsers]' for more)",
    ))
    # Note the escaped bracket: rich would otherwise parse "[server]" as markup.
    for module, label, extra in (
        ("fastapi", "dashboard server", r"bugseer\[server]"),
        ("lightgbm", "LightGBM", r"bugseer\[ml]"),
        ("xgboost", "XGBoost", r"bugseer\[ml]"),
        ("dotenv", ".env loading", r"bugseer\[env]"),
    ):
        try:
            __import__(module)
            table.add_row("", mark(True, label, ""))
        except ModuleNotFoundError:
            table.add_row("", mark(False, "", f"{label} (pip install '{extra}')"))

    from bugseer.narrate import detect_provider
    provider = detect_provider()
    table.add_row("AI narrator", (
        f"[magenta]{provider}[/magenta] [dim](optional, narration only)[/dim]"
        if provider != "none"
        else "[dim]○ disabled — fully offline[/dim]"
    ))
    console.print(Panel(table, title="BugSeer", border_style="cyan", expand=False))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
