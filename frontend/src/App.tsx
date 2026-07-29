import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { DependencyGraph } from "./components/DependencyGraph";
import { FileCard, RiskBar } from "./components/FileCard";
import { HeatMap } from "./components/HeatMap";
import type {
  FileRisk,
  GraphPayload,
  ImpactResult,
  ReportSummary,
} from "./types";

type Tab = "overview" | "files" | "heatmap" | "graph" | "impact";

export default function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [files, setFiles] = useState<FileRisk[]>([]);
  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [band, setBand] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const [seeds, setSeeds] = useState<string[]>([]);
  const [impact, setImpact] = useState<ImpactResult | null>(null);
  const [impactBusy, setImpactBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, f] = await Promise.all([api.summary(), api.files()]);
      setSummary(s);
      setFiles(f);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (tab === "graph" && !graph) {
      api.graph().then(setGraph).catch((e) => setError((e as Error).message));
    }
  }, [tab, graph]);

  async function rescan() {
    setBusy(true);
    setError(null);
    try {
      await api.refresh();
      setGraph(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function train() {
    setBusy(true);
    setError(null);
    try {
      const result = (await api.train(365)) as { trained?: boolean; reason?: string };
      if (!result.trained) setError(`Training skipped — ${result.reason}`);
      setGraph(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const addSeed = useCallback((path: string) => {
    setSeeds((current) => (current.includes(path) ? current : [...current, path]));
    setTab("impact");
  }, []);

  const runImpact = useCallback(async (paths: string[]) => {
    if (paths.length === 0) { setImpact(null); return; }
    setImpactBusy(true);
    try {
      setImpact(await api.impact(paths));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setImpactBusy(false);
    }
  }, []);

  useEffect(() => { void runImpact(seeds); }, [seeds, runImpact]);

  const filtered = useMemo(() => {
    const needle = query.toLowerCase().trim();
    const order = { critical: 3, high: 2, medium: 1, low: 0 } as const;
    const min = band ? order[band as keyof typeof order] : -1;
    return files.filter((f) => {
      if (order[f.band] < min) return false;
      if (!needle) return true;
      return (
        f.path.toLowerCase().includes(needle) ||
        f.hits.some(
          (h) => h.rule_id.includes(needle) || h.title.toLowerCase().includes(needle),
        )
      );
    });
  }, [files, query, band]);

  if (loading) {
    return (
      <div className="center">
        <div className="spinner" />
        <div>Analysing repository…</div>
        <div style={{ fontSize: 12 }}>Static analysis, git history and dependency graph — all local.</div>
      </div>
    );
  }

  const s = summary?.summary;

  return (
    <div className="app">
      <div className="topbar">
        <div className="logo">Bug<span>Seer</span></div>
        <div className="root-path" title={summary?.root}>{summary?.root}</div>
        <div className="tabs">
          {(["overview", "files", "heatmap", "graph", "impact"] as Tab[]).map((t) => (
            <button
              key={t}
              className={`tab${tab === t ? " active" : ""}`}
              onClick={() => setTab(t)}
            >
              {t === "impact" ? "What if?" : t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
        <div className="spacer" />
        <span className="offline-badge">● offline — nothing uploaded</span>
        <button onClick={train} disabled={busy}>Train model</button>
        <button className="primary" onClick={rescan} disabled={busy}>
          {busy ? "Working…" : "Re-scan"}
        </button>
      </div>

      <div className="content">
        <div className="wrap">
          {error && <div className="err">{error}</div>}

          {/* ------------------------------------------------ overview */}
          {tab === "overview" && s && (
            <>
              <div className="cards">
                <div className="card"><div className="v critical">{s.bands.critical}</div><div className="l">Critical</div></div>
                <div className="card"><div className="v high">{s.bands.high}</div><div className="l">High risk</div></div>
                <div className="card"><div className="v medium">{s.bands.medium}</div><div className="l">Medium</div></div>
                <div className="card"><div className="v low">{s.bands.low}</div><div className="l">Low</div></div>
                <div className="card"><div className="v">{s.average_score}</div><div className="l">Average score</div></div>
                <div className="card"><div className="v">{s.total_loc.toLocaleString()}</div><div className="l">Lines of code</div></div>
              </div>

              <div className="note">
                <strong>How these scores were produced.</strong>{" "}
                Phase 1 parsed {s.files_scanned} files (
                {Object.entries(s.parsers).map(([k, v]) => `${k} ×${v}`).join(", ")}).{" "}
                {s.git.available
                  ? `Phase 2 replayed ${s.git.commits_analyzed} commits, finding ${s.git.bugfix_commits} bug fixes and ${s.git.reverts} reverts.`
                  : `Phase 2 skipped — ${s.git.reason ?? "no git history"}.`}{" "}
                {s.ml?.trained
                  ? `Phase 3 trained ${s.ml.estimator.split(".").pop()} locally on ${s.ml.samples} files (${s.ml.positives} bug-fixed)${s.ml.auc ? `, out-of-fold AUC ${s.ml.auc}` : ""}.`
                  : summary?.ml_used
                    ? "Phase 3 used a cached local model."
                    : "Phase 3 inactive — click “Train model” to learn from this repo's bug history."}{" "}
                Phase 5 built {s.graph.import_edges} import edges and {s.graph.cochange_edges} co-change edges.
                Scan took {summary?.duration_seconds.toFixed(2)}s.
              </div>

              {s.git.degraded && <div className="note warn">⚠ {s.git.degraded_reason}</div>}

              <h2>Top hotspots</h2>
              {summary?.hotspots.slice(0, 10).map((h) => (
                <div className="file" key={h.path}>
                  <div className="fhead" onClick={() => { setExpanded(h.path); setTab("files"); }}>
                    <div>{h.band === "critical" ? "🔥" : h.band === "high" ? "🔴" : h.band === "medium" ? "🟡" : "🟢"}</div>
                    <div className={`score ${h.band}`}>{h.score.toFixed(0)}</div>
                    <RiskBar score={h.score} band={h.band} />
                    <div className="fpath">{h.path}</div>
                    <div className="tags">{h.reasons.join(" · ")}</div>
                  </div>
                </div>
              ))}

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22, marginTop: 8 }}>
                <div>
                  <h2>Most common findings</h2>
                  <table className="plain">
                    <tbody>
                      {s.top_rules.map((r) => (
                        <tr key={r.rule_id}>
                          <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{r.rule_id}</td>
                          <td>{r.files} files</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {s.ml?.trained && s.ml.top_features.length > 0 && (
                  <div>
                    <h2>What the model learned</h2>
                    <table className="plain">
                      <tbody>
                        {s.ml.top_features.slice(0, 8).map((f) => (
                          <tr key={f.feature}>
                            <td>{f.label}</td>
                            <td>
                              <span className="featbar" style={{ width: `${f.importance * 110}px` }} />{" "}
                              {(f.importance * 100).toFixed(0)}%
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}

          {/* --------------------------------------------------- files */}
          {tab === "files" && (
            <>
              <div className="toolbar">
                <input
                  type="search"
                  placeholder="Filter by path, rule id or finding…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <select value={band} onChange={(e) => setBand(e.target.value)}>
                  <option value="">All bands</option>
                  <option value="critical">Critical only</option>
                  <option value="high">High and above</option>
                  <option value="medium">Medium and above</option>
                </select>
                <span style={{ color: "var(--dim)", fontSize: 12.5 }}>
                  {filtered.length} file{filtered.length === 1 ? "" : "s"}
                </span>
              </div>
              {filtered.length === 0 && <div className="empty">Nothing matches that filter.</div>}
              {filtered.map((file) => (
                <FileCard
                  key={file.path}
                  file={file}
                  expanded={expanded === file.path}
                  onToggle={() => setExpanded(expanded === file.path ? null : file.path)}
                  onImpact={addSeed}
                />
              ))}
            </>
          )}

          {/* ------------------------------------------------- heatmap */}
          {tab === "heatmap" && (
            <>
              <div className="note">
                Every file in the project, grouped by directory and coloured by risk.
                Click a file to open its full evidence.
              </div>
              <HeatMap
                files={files}
                selected={expanded}
                onSelect={(path) => { setExpanded(path); setTab("files"); }}
              />
            </>
          )}

          {/* --------------------------------------------------- graph */}
          {tab === "graph" && (
            <>
              <div className="note">
                Import graph. Node size reflects how connected a file is, colour reflects its
                risk. Files that are both large and red are the ones a mistake propagates from.
              </div>
              {graph ? (
                <DependencyGraph
                  data={graph}
                  onSelect={(path) => { setExpanded(path); setTab("files"); }}
                />
              ) : (
                <div className="center"><div className="spinner" /></div>
              )}
            </>
          )}

          {/* -------------------------------------------------- impact */}
          {tab === "impact" && (
            <>
              <div className="note">
                <strong>“What if?” simulator.</strong> Pick the file(s) you are about to change.
                BugSeer combines the import graph with historical co-change to predict what is
                most likely to break, and explains each prediction.
              </div>

              <div className="toolbar">
                <select
                  value=""
                  onChange={(e) => { if (e.target.value) addSeed(e.target.value); }}
                >
                  <option value="">Add a file to the change set…</option>
                  {files.map((f) => (
                    <option key={f.path} value={f.path}>{f.path}</option>
                  ))}
                </select>
                {seeds.length > 0 && <button onClick={() => setSeeds([])}>Clear</button>}
              </div>

              {seeds.length > 0 && (
                <div className="seedbar">
                  {seeds.map((seed) => (
                    <span className="seed" key={seed}>
                      {seed}
                      <button onClick={() => setSeeds(seeds.filter((x) => x !== seed))}>×</button>
                    </span>
                  ))}
                </div>
              )}

              {impactBusy && <div className="center"><div className="spinner" /></div>}

              {!impactBusy && impact && (
                <>
                  <h2>
                    {impact.affected.length} file{impact.affected.length === 1 ? "" : "s"} likely affected
                  </h2>
                  <div className="impact-grid">
                    {impact.affected.map((item) => {
                      const b =
                        item.impact_score >= 70 ? "critical" :
                        item.impact_score >= 45 ? "high" :
                        item.impact_score >= 25 ? "medium" : "low";
                      return (
                        <div className="impact-row" key={item.path}>
                          <div className={`score ${b}`}>{item.impact_score.toFixed(0)}</div>
                          <RiskBar score={item.impact_score} band={b} />
                          <div>
                            <div className="fpath">{item.path}</div>
                            <div className="impact-why">{item.reasons.join("; ")}</div>
                          </div>
                          <div style={{ color: "var(--dim)", fontSize: 12, whiteSpace: "nowrap" }}>
                            own risk {item.own_risk.toFixed(0)} · {item.hops} hop
                            {item.hops === 1 ? "" : "s"}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  {impact.affected.length === 0 && (
                    <div className="empty">
                      No coupled files detected — this change looks well isolated.
                    </div>
                  )}
                  <div style={{ marginTop: 14, color: "var(--dim)", fontSize: 12.5 }}>
                    {impact.explanation.map((line, i) => <div key={i}>· {line}</div>)}
                  </div>
                </>
              )}

              {!impactBusy && seeds.length === 0 && (
                <div className="empty">Select a file above to simulate a change.</div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
