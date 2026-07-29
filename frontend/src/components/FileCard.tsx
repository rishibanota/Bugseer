import { useState } from "react";
import type { FileRisk } from "../types";

const EMOJI: Record<string, string> = {
  low: "🟢",
  medium: "🟡",
  high: "🔴",
  critical: "🔥",
};

export function RiskBar({ score, band }: { score: number; band: string }) {
  return (
    <div className="bar">
      <i className={`bg-${band}`} style={{ width: `${Math.max(2, score)}%` }} />
    </div>
  );
}

interface Props {
  file: FileRisk;
  expanded?: boolean;
  onToggle?: () => void;
  onImpact?: (path: string) => void;
}

/**
 * A single file row. Collapsed it shows the score and top reasons; expanded it
 * shows every piece of evidence that produced the score - which is the whole
 * point of the tool, so nothing here is hidden behind a tooltip.
 */
export function FileCard({ file, expanded, onToggle, onImpact }: Props) {
  const [narration, setNarration] = useState<string | null>(null);
  const [narrating, setNarrating] = useState(false);

  const tags = file.hits.slice(0, 3).map((h) => h.title).join(" · ") || "no rules triggered";

  async function runNarrate(event: React.MouseEvent) {
    event.stopPropagation();
    setNarrating(true);
    try {
      const { api } = await import("../api");
      const result = await api.narrate(file.path);
      setNarration(result.ok ? result.text : `⚠ ${result.error}`);
    } catch (error) {
      setNarration(`⚠ ${(error as Error).message}`);
    } finally {
      setNarrating(false);
    }
  }

  return (
    <div className={`file${expanded ? " selected" : ""}`}>
      <div className="fhead" onClick={onToggle}>
        <div>{EMOJI[file.band]}</div>
        <div className={`score ${file.band}`}>{file.score.toFixed(0)}</div>
        <RiskBar score={file.score} band={file.band} />
        <div className="fpath">{file.path}</div>
        <div className="tags">{tags}</div>
      </div>

      {expanded && (
        <div className="fbody">
          <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
            <span className="chip">raw {file.raw_score.toFixed(0)} pts</span>
            {file.static_score > 0 && <span className="chip static">static {file.static_score.toFixed(0)}</span>}
            {file.git_score > 0 && <span className="chip git">git {file.git_score.toFixed(0)}</span>}
            {file.graph_score > 0 && <span className="chip graph">graph {file.graph_score.toFixed(0)}</span>}
            {file.ml_probability !== null && (
              <span className="chip ml">model {(file.ml_probability * 100).toFixed(0)}%</span>
            )}
            <div className="spacer" style={{ flex: 1 }} />
            {onImpact && (
              <button onClick={(e) => { e.stopPropagation(); onImpact(file.path); }}>
                What if I change this?
              </button>
            )}
            <button onClick={runNarrate} disabled={narrating}>
              {narrating ? "Asking…" : "AI summary"}
            </button>
          </div>

          {narration && (
            <div className="note" style={{ whiteSpace: "pre-wrap" }}>
              {narration}
              <div style={{ marginTop: 6, fontSize: 11 }}>
                Narration only — the score above was computed locally and is unaffected.
              </div>
            </div>
          )}

          {file.hits.length === 0 && <div className="empty">No rules triggered.</div>}

          {file.hits.map((hit, index) => (
            <div className="hit" key={`${hit.rule_id}-${index}`}>
              <div>
                <span className={`pts ${file.band}`}>+{hit.score.toFixed(0)}</span>
                <span className="t">{hit.title}</span>
                <span className={`chip ${hit.phase}`}>{hit.phase}</span>
                <span className="chip">{hit.rule_id}</span>
              </div>
              <div className="d">{hit.detail}</div>
              {hit.locations?.length > 0 && (
                <div className="loc">
                  ↳{" "}
                  {hit.locations.slice(0, 6).map((loc, i) => (
                    <span key={i}>
                      {i > 0 && ", "}
                      L{loc.line}
                      {loc.name ? ` ${loc.name}` : ""}
                      {loc.note ? ` (${loc.note})` : ""}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}

          {file.ml_contributions?.length > 0 && (
            <div className="mlbox">
              <div style={{ fontWeight: 620, marginBottom: 5 }}>
                Local model reasoning
                {file.ml_probability !== null &&
                  ` — ${(file.ml_probability * 100).toFixed(0)}% probability of needing a fix`}
              </div>
              {file.ml_contributions.slice(0, 5).map((c) => (
                <div className="mlrow" key={c.feature}>
                  <span className={c.direction === "increases" ? "high" : "low"}>
                    {c.direction === "increases" ? "▲" : "▼"} {c.label}
                  </span>
                  <span>
                    <strong>{c.value}</strong>{" "}
                    <span style={{ color: "var(--dim)" }}>
                      ({c.z_score >= 0 ? "+" : ""}
                      {c.z_score.toFixed(1)}σ vs repo average)
                    </span>
                  </span>
                </div>
              ))}
            </div>
          )}

          {file.dependents?.length > 0 && (
            <div className="coupling">
              ⇄ {file.dependents.length} file(s) import this:{" "}
              {file.dependents.slice(0, 6).join(", ")}
              {file.dependents.length > 6 ? "…" : ""}
            </div>
          )}
          {file.git?.co_change_partners && file.git.co_change_partners.length > 0 && (
            <div className="coupling">
              ⎇ usually changes alongside:{" "}
              {file.git.co_change_partners
                .slice(0, 4)
                .map((p) => `${p.path} (${(p.strength * 100).toFixed(0)}%)`)
                .join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
