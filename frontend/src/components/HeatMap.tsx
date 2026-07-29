import { useMemo } from "react";
import type { FileRisk } from "../types";

const EMOJI: Record<string, string> = {
  low: "🟢",
  medium: "🟡",
  high: "🔴",
  critical: "🔥",
};

/**
 * Phase 4: the project heat map. Files are grouped by directory and each
 * directory reports its worst child, so a problem area is visible without
 * expanding anything.
 */
export function HeatMap({
  files,
  selected,
  onSelect,
}: {
  files: FileRisk[];
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  const grouped = useMemo(() => {
    const map = new Map<string, FileRisk[]>();
    for (const file of files) {
      const parts = file.path.split("/");
      const dir = parts.slice(0, -1).join("/") || ".";
      const list = map.get(dir) ?? [];
      list.push(file);
      map.set(dir, list);
    }
    return [...map.entries()]
      .map(([dir, list]) => ({
        dir,
        files: [...list].sort((a, b) => b.score - a.score),
        worst: Math.max(...list.map((f) => f.score)),
      }))
      .sort((a, b) => b.worst - a.worst);
  }, [files]);

  if (files.length === 0) {
    return <div className="empty">No files match the current filter.</div>;
  }

  return (
    <div className="tree">
      {grouped.map(({ dir, files: list, worst }) => {
        const band =
          worst >= 85 ? "critical" : worst >= 60 ? "high" : worst >= 35 ? "medium" : "low";
        return (
          <div className="tree-dir" key={dir}>
            <div>
              <strong>{dir}/</strong>{" "}
              <span style={{ color: "var(--dim)" }}>
                {list.length} file{list.length === 1 ? "" : "s"} · worst{" "}
              </span>
              <span className={band}>{worst.toFixed(0)}</span>
            </div>
            {list.map((file) => (
              <div
                key={file.path}
                className={`tree-file${selected === file.path ? " sel" : ""}`}
                onClick={() => onSelect(file.path)}
                title={file.hits.slice(0, 3).map((h) => h.title).join(" · ")}
              >
                {EMOJI[file.band]} {file.path.split("/").pop()}{" "}
                <span className={file.band}>{file.score.toFixed(0)}</span>{" "}
                <span style={{ color: "var(--dim)" }}>
                  {file.hits.slice(0, 2).map((h) => h.title).join(" · ")}
                </span>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
