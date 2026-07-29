"""Phase 4/5: the local dashboard API.

Binds to localhost by default. Serves the built React frontend if present,
otherwise falls back to the self-contained HTML report so the dashboard works
even without a node toolchain.
"""

from __future__ import annotations

import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from bugseer import __version__
from bugseer.config import load_config
from bugseer.graph import simulate_impact
from bugseer.models import RepoReport
from bugseer.scanner import Scanner


class ScanCache:
    """Holds the most recent scan so the UI does not re-analyse on every request."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.report: RepoReport | None = None
        self.scanner: Scanner | None = None
        self.scanning = False
        self.last_error: str = ""
        self.updated_at: float = 0.0
        self._lock = threading.Lock()

    def ensure(self, *, force: bool = False) -> RepoReport:
        with self._lock:
            if self.report is not None and not force:
                return self.report
            self.scanning = True
        try:
            cfg = load_config(self.root)
            scanner = Scanner(cfg)
            report = scanner.scan(train=False, use_model=True)
            with self._lock:
                self.report = report
                self.scanner = scanner
                self.updated_at = time.time()
                self.last_error = ""
            return report
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            with self._lock:
                self.scanning = False


def _impact_request_model():
    """Build the request model at module scope.

    FastAPI resolves handler annotations against module globals, so a pydantic
    model defined inside `create_app` is invisible to it and silently degrades
    into a query parameter. Defining it here keeps the body schema correct.
    """
    from pydantic import BaseModel

    class ImpactRequest(BaseModel):
        files: list[str]
        hops: int = 3
        limit: int = 25

    return ImpactRequest


ImpactRequest = _impact_request_model()


def create_app(root: Path):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    root = Path(root).resolve()
    cache = ScanCache(root)

    app = FastAPI(
        title="BugSeer",
        version=__version__,
        description="Local bug-risk analysis API. Runs offline; nothing is uploaded.",
    )
    # The dev frontend runs on a different port; the server is localhost-only.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------------------------------------------------------------- meta
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "root": str(root),
            "scanning": cache.scanning,
            "has_report": cache.report is not None,
            "updated_at": cache.updated_at,
        }

    # -------------------------------------------------------------- report
    @app.get("/api/report")
    def get_report(refresh: bool = Query(False), metrics: bool = Query(False)) -> Any:
        try:
            report = cache.ensure(force=refresh)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse(report.to_dict(include_metrics=metrics))

    @app.get("/api/summary")
    def get_summary() -> Any:
        report = cache.ensure()
        return {
            "root": report.root,
            "generated_at": report.generated_at,
            "summary": report.summary,
            "hotspots": report.hotspots,
            "git_available": report.git_available,
            "ml_used": report.ml_used,
            "duration_seconds": report.duration_seconds,
            "version": report.version,
        }

    @app.get("/api/files")
    def list_files(
        band: str | None = None,
        min_score: float = 0.0,
        limit: int = 500,
        q: str | None = None,
    ) -> Any:
        report = cache.ensure()
        files = [f for f in report.files if f.score >= min_score]
        if band:
            files = [f for f in files if f.band == band]
        if q:
            needle = q.lower()
            files = [
                f for f in files
                if needle in f.path.lower()
                or any(needle in h.rule_id or needle in h.title.lower() for h in f.hits)
            ]
        return [f.to_dict(include_metrics=False) for f in files[:limit]]

    @app.get("/api/file")
    def get_file(path: str) -> Any:
        report = cache.ensure()
        target = report.by_path(path)
        if target is None:
            matches = [f for f in report.files if f.path.endswith(path)]
            if len(matches) != 1:
                raise HTTPException(status_code=404, detail=f"File not found: {path}")
            target = matches[0]
        payload = target.to_dict(include_metrics=True)
        source_path = root / target.path
        try:
            if source_path.is_file() and source_path.stat().st_size < 400_000:
                payload["source"] = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        return payload

    # --------------------------------------------------------------- graph
    @app.get("/api/graph")
    def get_graph(limit: int = 300) -> Any:
        report = cache.ensure()
        if cache.scanner is None:
            return {"nodes": [], "edges": []}
        return cache.scanner.graph_json({f.path: f.score for f in report.files})

    @app.get("/api/tree")
    def get_tree() -> Any:
        """Directory-rolled-up view for the heat map."""
        report = cache.ensure()
        tree: dict[str, Any] = {}
        for f in report.files:
            parts = f.path.split("/")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {"__dir__": True, "children": {}})["children"]
            node[parts[-1]] = {
                "__dir__": False,
                "path": f.path,
                "score": f.score,
                "band": f.band,
                "reasons": [h.title for h in f.top_reasons(3)],
            }
        return tree

    # -------------------------------------------------------------- impact
    @app.post("/api/impact")
    def post_impact(request: ImpactRequest) -> Any:
        report = cache.ensure()
        if cache.scanner is None or cache.scanner.graph is None:
            raise HTTPException(status_code=503, detail="Dependency graph unavailable")
        risk_by_path = {f.path: f.score for f in report.files}
        resolved: list[str] = []
        for wanted in request.files:
            if wanted in risk_by_path:
                resolved.append(wanted)
            else:
                matches = [p for p in risk_by_path if p.endswith(wanted)]
                if len(matches) == 1:
                    resolved.append(matches[0])
        result = simulate_impact(
            cache.scanner.graph, resolved,
            risk_by_path=risk_by_path,
            cochange_strength=cache.scanner.cochange_strength(),
            max_hops=request.hops, limit=request.limit,
        )
        return result.to_dict()

    # ------------------------------------------------------------- narrate
    @app.post("/api/narrate")
    def post_narrate(path: str) -> Any:
        """Optional AI prose summary. Returns a clear message when unconfigured."""
        from bugseer.narrate import narrate

        report = cache.ensure()
        target = report.by_path(path)
        if target is None:
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        result = narrate(target)
        return {"ok": result.ok, "provider": result.provider,
                "text": result.text, "error": result.error}

    # --------------------------------------------------------------- train
    @app.post("/api/train")
    def post_train(label_window: int = 180) -> Any:
        from bugseer.analysis.static import analyze_file
        from bugseer.graph import build_dependency_graph
        from bugseer.ml import BugPredictor
        from bugseer.scanner import discover_files

        cfg = load_config(root)
        files = discover_files(cfg)
        metrics_by_path = {}
        for abs_path, rel_path, language in files:
            metrics, _ = analyze_file(abs_path, rel_path, language, cfg.max_file_bytes)
            metrics_by_path[rel_path] = metrics
        graph = build_dependency_graph(metrics_by_path)
        degrees = {p: (graph.out_degree(p), graph.in_degree(p)) for p in metrics_by_path}

        predictor = BugPredictor(cfg.home_path)
        training = predictor.train(
            cfg.root, metrics_by_path, degrees, label_window_days=label_window
        )
        if training.trained:
            predictor.save()
            cache.ensure(force=True)
        return training.to_dict()

    # ------------------------------------------------------------ frontend
    # Built by `npm run build` in frontend/. Named "static" rather than "dist"
    # so it survives packaging tools that ignore build directories.
    dist = Path(__file__).parent / "webui" / "static"
    if dist.is_dir() and (dist / "index.html").is_file():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/", response_class=HTMLResponse)
        def index() -> str:
            return (dist / "index.html").read_text(encoding="utf-8")

        @app.get("/{full_path:path}", response_class=HTMLResponse)
        def spa(full_path: str) -> str:
            candidate = dist / full_path
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace")
            return (dist / "index.html").read_text(encoding="utf-8")
    else:
        @app.get("/", response_class=HTMLResponse)
        def fallback_index() -> str:
            """Serve the static report when the React bundle has not been built."""
            from bugseer.report import HTML_TEMPLATE, _render_cards, _render_file
            from bugseer.report import _render_provenance, _render_tree, _esc

            report = cache.ensure()
            s = report.summary
            return HTML_TEMPLATE.format(
                root_name=_esc(Path(report.root).name or report.root),
                files_scanned=s.get("files_scanned", 0),
                total_loc=f"{s.get('total_loc', 0):,}",
                generated_at=_esc(report.generated_at),
                version=_esc(report.version),
                cards=_render_cards(report),
                provenance=(
                    _render_provenance(report)
                    + " <br><br><strong>Note:</strong> the React dashboard is not built. "
                    "Run <code>cd frontend && npm install && npm run build</code> for the "
                    "full interactive UI, or keep using this static view."
                ),
                tree=_render_tree(report),
                files="".join(_render_file(f) for f in report.files[:300]),
            )

    return app


def run_server(root: Path, host: str = "127.0.0.1", port: int = 8420,
               open_browser: bool = True) -> None:
    import uvicorn

    app = create_app(root)
    if open_browser:
        def _open() -> None:
            time.sleep(1.2)
            try:
                webbrowser.open(f"http://{host}:{port}")
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")
