import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphPayload } from "../types";

interface Node {
  id: string;
  risk: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
}

const COLOR = (risk: number) =>
  risk >= 85 ? "#ff6ac1" : risk >= 60 ? "#f85149" : risk >= 35 ? "#d29922" : "#3fb950";

/**
 * A dependency graph rendered with a small force-directed layout.
 *
 * Written by hand rather than pulling in D3 or Cytoscape: the layout is a few
 * dozen lines, it keeps the offline bundle tiny, and it avoids a heavyweight
 * dependency for what is essentially a scatter plot with springs.
 */
export function DependencyGraph({
  data,
  onSelect,
}: {
  data: GraphPayload;
  onSelect: (path: string) => void;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [, forceRender] = useState(0);
  const [hover, setHover] = useState<string | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  const width = 1200;
  const height = 560;

  const { nodes, links } = useMemo(() => {
    const ids = new Set(data.nodes.map((n) => n.id));
    const nodeList: Node[] = data.nodes.map((n, i) => {
      const angle = (i / Math.max(1, data.nodes.length)) * Math.PI * 2;
      const radius = 130 + (i % 9) * 26;
      return {
        id: n.id,
        risk: n.risk,
        x: width / 2 + Math.cos(angle) * radius,
        y: height / 2 + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        r: Math.min(15, 3.5 + Math.sqrt(n.in + n.out) * 1.9),
      };
    });
    const index = new Map(nodeList.map((n) => [n.id, n]));
    const linkList = data.edges
      .filter((e) => ids.has(e.source) && ids.has(e.target) && e.kind === "import")
      .map((e) => ({ s: index.get(e.source)!, t: index.get(e.target)! }));
    return { nodes: nodeList, links: linkList };
  }, [data]);

  // Force simulation: repulsion between nodes, springs along edges, gentle
  // gravity to the centre. Runs for a fixed number of ticks then stops.
  useEffect(() => {
    if (nodes.length === 0) return;
    let frame = 0;
    let raf = 0;

    const tick = () => {
      const alpha = Math.max(0.02, 0.55 * (1 - frame / 260));
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          let dist2 = dx * dx + dy * dy;
          if (dist2 < 1) {
            dx = Math.random() - 0.5;
            dy = Math.random() - 0.5;
            dist2 = 1;
          }
          if (dist2 > 160000) continue;
          const force = (2600 * alpha) / dist2;
          const dist = Math.sqrt(dist2);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx -= fx; a.vy -= fy;
          b.vx += fx; b.vy += fy;
        }
      }
      for (const { s, t } of links) {
        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - 105) * 0.010 * alpha * 10;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        s.vx += fx; s.vy += fy;
        t.vx -= fx; t.vy -= fy;
      }
      for (const n of nodes) {
        n.vx += (width / 2 - n.x) * 0.0009 * alpha * 10;
        n.vy += (height / 2 - n.y) * 0.0009 * alpha * 10;
        n.vx *= 0.82;
        n.vy *= 0.82;
        n.x += n.vx;
        n.y += n.vy;
      }
      frame++;
      forceRender((v) => v + 1);
      if (frame < 260) raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [nodes, links]);

  function onWheel(event: React.WheelEvent) {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.12 : 0.89;
    setView((v) => ({ ...v, k: Math.min(4, Math.max(0.3, v.k * factor)) }));
  }

  function onMouseDown(event: React.MouseEvent) {
    drag.current = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
  }
  function onMouseMove(event: React.MouseEvent) {
    if (!drag.current) return;
    setView((v) => ({
      ...v,
      x: drag.current!.vx + (event.clientX - drag.current!.x),
      y: drag.current!.vy + (event.clientY - drag.current!.y),
    }));
  }
  function endDrag() {
    drag.current = null;
  }

  if (data.nodes.length === 0) {
    return <div className="empty">No dependency edges were resolved for this project.</div>;
  }

  return (
    <div className="graph-holder">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        onWheel={onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
      >
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {links.map((l, i) => (
            <line
              key={i}
              x1={l.s.x} y1={l.s.y} x2={l.t.x} y2={l.t.y}
              stroke={hover && (l.s.id === hover || l.t.id === hover) ? "#58a6ff" : "#30363d"}
              strokeWidth={hover && (l.s.id === hover || l.t.id === hover) ? 1.6 : 0.7}
              opacity={hover ? (l.s.id === hover || l.t.id === hover ? 0.95 : 0.18) : 0.55}
            />
          ))}
          {nodes.map((n) => (
            <circle
              key={n.id}
              cx={n.x} cy={n.y} r={n.r}
              fill={COLOR(n.risk)}
              stroke={hover === n.id ? "#e6edf3" : "#0d1117"}
              strokeWidth={hover === n.id ? 2 : 1}
              opacity={hover && hover !== n.id ? 0.45 : 1}
              onMouseEnter={() => setHover(n.id)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect(n.id)}
              style={{ cursor: "pointer" }}
            >
              <title>{`${n.id}\nrisk ${n.risk}`}</title>
            </circle>
          ))}
          {hover && (() => {
            const n = nodes.find((x) => x.id === hover);
            if (!n) return null;
            const label = n.id.split("/").slice(-2).join("/");
            return (
              <text
                x={n.x + n.r + 6} y={n.y + 4}
                fill="#e6edf3" fontSize={11} fontFamily="ui-monospace, monospace"
                style={{ pointerEvents: "none" }}
              >
                {label} ({n.risk})
              </text>
            );
          })()}
        </g>
      </svg>
      <div style={{ padding: "9px 14px", color: "var(--dim)", fontSize: 12, borderTop: "1px solid var(--border)" }}>
        {data.nodes.length} nodes · {links.length} import edges · scroll to zoom, drag to pan,
        click a node to inspect it{data.truncated ? " · showing the highest-risk subset" : ""}
      </div>
    </div>
  );
}
