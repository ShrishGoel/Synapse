import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

// ═══════════════════════════════════════════════════════════════
// UNIFIED SESSION SCHEMA — single payload from Layer 2
// ═══════════════════════════════════════════════════════════════
const SESSION = {
  session_id: "ses_abc123",
  query: "Compare these by price, commute, and landlord risk",
  domain: "housing",
  status: "ready",

  // ── GRAPH (Board View) ──
  graph: {
    nodes: [
      { id: "n1", type: "listing", source: "Zillow", title: "1824 Ashbury Ave", subtitle: "2 bed · $2,780/mo", status: "ready", group: "options", tags: ["pet-friendly", "in-unit laundry"], metadata: { price: 2780, bedrooms: 2, commute_minutes: 14, commute_mode: "walk" }, summary: "Bright two-bedroom with in-unit laundry and strong walkability." },
      { id: "n2", type: "listing", source: "Zillow", title: "99 Linden Street", subtitle: "Studio · $2,120/mo", status: "ready", group: "options", tags: ["no parking"], metadata: { price: 2120, bedrooms: 0, commute_minutes: 19, commute_mode: "transit" }, summary: "Compact studio loft with the best commute and lowest price." },
      { id: "n3", type: "listing", source: "Apartments.com", title: "Harbor Flats", subtitle: "1 bed · $2,640/mo", status: "extracting", group: "options", tags: ["gym", "1 month free", "strict guest clause"], metadata: { price: 2640, bedrooms: 1, commute_minutes: 12, commute_mode: "drive" }, summary: "Amenity-rich managed building with competitive incentives." },
      { id: "n4", type: "review", source: "Reddit", title: "Harbor Flats Mgmt Thread", subtitle: "7 complaints", status: "ready", group: "context", tags: ["maintenance delays", "deposit friction"], metadata: { complaint_count: 7, sentiment_score: -0.72 }, summary: "Tenant commentary clusters around maintenance delays and deposit issues." },
      { id: "n5", type: "location", source: "Google Maps", title: "West Oakland Area", subtitle: "Transit hub", status: "ready", group: "context", tags: ["transit strong", "bike lanes", "noise varies"], metadata: { transit_score: 82, walk_score: 68 }, summary: "Strong transit access. Daily errands manageable. Noise varies by block." },
      { id: "n6", type: "enrichment", source: "Firecrawl", title: "Utility Cost Baseline", subtitle: "Auto-fetched", status: "pending", group: "enrichment", tags: ["auto-fetched"], metadata: {}, summary: "Queued for background enrichment via Firecrawl." },
    ],
    edges: [
      { id: "e1", from: "n1", to: "n5", label: "located_in", strength: 0.9 },
      { id: "e2", from: "n2", to: "n5", label: "located_in", strength: 0.85 },
      { id: "e3", from: "n3", to: "n5", label: "located_in", strength: 0.8 },
      { id: "e4", from: "n4", to: "n3", label: "mentions", strength: 0.95 },
      { id: "e5", from: "n1", to: "n2", label: "competes_with", strength: 0.7 },
      { id: "e6", from: "n1", to: "n3", label: "competes_with", strength: 0.65 },
      { id: "e7", from: "n6", to: "n5", label: "enriches", strength: 0.5 },
    ],
  },

  // ── MATRIX (Compare View) ──
  matrix: {
    rubric: "Housing Comparison",
    columns: [
      { key: "price", label: "Monthly Cost", type: "currency", highlight_best: true },
      { key: "commute", label: "Commute", type: "duration" },
      { key: "trust", label: "Trust Signal", type: "sentiment" },
      { key: "friction", label: "Friction", type: "text" },
    ],
    rows: [
      { node_id: "n1", cells: { price: { value: 2780, display: "$2,780", rank: 3 }, commute: { value: 14, display: "14m walk", rank: 2 }, trust: { value: 0, display: "Neutral", sentiment: "neutral" }, friction: { value: null, display: "Pet deposit" } } },
      { node_id: "n2", cells: { price: { value: 2120, display: "$2,120", rank: 1 }, commute: { value: 19, display: "19m transit", rank: 3 }, trust: { value: null, display: "Sparse data", sentiment: "unknown" }, friction: { value: null, display: "No parking" } } },
      { node_id: "n3", cells: { price: { value: 2640, display: "$2,640", rank: 2 }, commute: { value: 12, display: "12m drive", rank: 1 }, trust: { value: -0.72, display: "Negative (7 complaints)", sentiment: "negative" }, friction: { value: null, display: "Guest clause" } } },
    ],
  },

  // ── DIGEST ──
  digest: {
    theme: "Housing comparison",
    theme_signals: ["price", "commute", "landlord risk"],
    stats: { total: 6, ready: 4, extracting: 1, pending: 1 },
    entries: [
      { node_id: "n1", relevance: 0.95, summary: "Bright two-bedroom apartment with in-unit laundry, strong walkability, and a slightly longer transit commute.", signals: [{ label: "Rent $2,780", kind: "price" }, { label: "14m walk to BART", kind: "commute" }, { label: "Pet deposit", kind: "friction" }], source_note: "High confidence" },
      { node_id: "n2", relevance: 0.90, summary: "Compact studio loft with the best commute and lowest price, but noticeably thinner amenity set.", signals: [{ label: "Rent $2,120", kind: "price" }, { label: "19m transit", kind: "commute" }, { label: "No parking", kind: "friction" }], source_note: "High confidence" },
      { node_id: "n3", relevance: 0.88, summary: "Amenity-rich managed building with competitive incentives, offset by stricter lease tone.", signals: [{ label: "Rent $2,640", kind: "price" }, { label: "12m drive", kind: "commute" }, { label: "1 month free", kind: "perk" }, { label: "Guest clause", kind: "friction" }], source_note: "High confidence" },
      { node_id: "n4", relevance: 0.82, summary: "Tenant commentary clusters around maintenance delays and deposit issues.", signals: [{ label: "7 complaints", kind: "volume" }, { label: "Maintenance delays", kind: "risk" }, { label: "Deposit friction", kind: "risk" }], source_note: "Inferred from thread" },
      { node_id: "n5", relevance: 0.60, summary: "Strong transit access, daily errands manageable, noise varies by block.", signals: [{ label: "Transit score 82", kind: "commute" }, { label: "Noise varies", kind: "risk" }], source_note: "Partial extraction" },
      { node_id: "n6", relevance: 0.20, summary: "Queued for background enrichment via Firecrawl.", signals: [{ label: "Pending", kind: "status" }], source_note: "Pending enrichment" },
    ],
  },
};

// ── Layout positions for graph nodes ──
const POS = { n1: [18, 22], n2: [55, 15], n3: [42, 52], n4: [72, 48], n5: [28, 72], n6: [68, 78] };

// ── Style constants ──
const TYPE_CLR = { listing: "#3b82f6", review: "#ef4444", location: "#22c55e", enrichment: "#a855f7", reference: "#f59e0b" };
const SENT_CLR = { positive: "#22c55e", neutral: "#6b7280", negative: "#ef4444", unknown: "#eab308" };
const STAT_CLR = { ready: "#22c55e", extracting: "#eab308", pending: "#6b7280" };
const EDGE_CLR = { mentions: "rgba(239,68,68,0.35)", competes_with: "rgba(59,130,246,0.2)", located_in: "rgba(255,255,255,0.08)", enriches: "rgba(168,85,247,0.2)" };

const pill = { padding: "4px 10px", borderRadius: 999, fontSize: 12, background: "rgba(255,255,255,0.06)", color: "#d1d5db", border: "1px solid rgba(255,255,255,0.06)" };
const card = { borderRadius: 14, border: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.03)" };
const label = { fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", color: "#6b7280", marginBottom: 8 };

function Badge({ sentiment, children }) {
  const bg = { positive: "rgba(34,197,94,0.15)", neutral: "rgba(107,114,128,0.15)", negative: "rgba(239,68,68,0.15)", unknown: "rgba(234,179,8,0.15)" };
  return <span style={{ ...pill, background: bg[sentiment] || bg.unknown, color: SENT_CLR[sentiment] || SENT_CLR.unknown, fontWeight: 500 }}>{children}</span>;
}

const VIEWS = ["board", "digest", "compare"];

export default function App() {
  const [selected, setSelected] = useState(null);
  const [query, setQuery] = useState(SESSION.query);
  const [view, setView] = useState("all");
  const show = (v) => view === "all" || view === v;
  const data = SESSION; // In production, this would be reactive state from Layer 2
  const nodeMap = Object.fromEntries(data.graph.nodes.map(n => [n.id, n]));
  const sel = selected ? nodeMap[selected] : null;
  const toggle = (id) => setSelected(prev => prev === id ? null : id);

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#e5e7eb", fontFamily: "'Inter', system-ui, sans-serif", position: "relative", overflow: "hidden" }}>
      {/* Ambient */}
      <div style={{ position: "absolute", top: "-20%", left: "-10%", width: "50%", height: "50%", background: "rgba(59,130,246,0.06)", filter: "blur(140px)", borderRadius: "50%", pointerEvents: "none" }} />
      <div style={{ position: "absolute", bottom: "-20%", right: "-10%", width: "50%", height: "50%", background: "rgba(168,85,247,0.06)", filter: "blur(140px)", borderRadius: "50%", pointerEvents: "none" }} />

      {/* Navbar */}
      <header style={{ position: "sticky", top: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 24px", borderBottom: "1px solid rgba(255,255,255,0.06)", background: "rgba(0,0,0,0.6)", backdropFilter: "blur(12px)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 30, height: 30, borderRadius: 8, background: "linear-gradient(135deg,#3b82f6,#a855f7)", display: "grid", placeItems: "center", fontWeight: 700, fontSize: 13, color: "#fff" }}>S</div>
          <span style={{ fontWeight: 600, fontSize: 17, letterSpacing: "-0.02em" }}>Synapse</span>
          <span style={{ fontSize: 12, color: "#6b7280", marginLeft: 8 }}>· {data.domain}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#22c55e" }} />
          <span style={{ fontSize: 12, color: "#6b7280" }}>{data.digest.stats.ready}/{data.digest.stats.total} ready</span>
        </div>
      </header>

      {/* Query Bar */}
      <div style={{ padding: "16px 24px", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
        <form onSubmit={e => e.preventDefault()} style={{ position: "relative", maxWidth: 700 }}>
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Ask a question about your tabs..." style={{ width: "100%", padding: "12px 16px", borderRadius: 12, border: "1px solid rgba(255,255,255,0.08)", background: "rgba(255,255,255,0.04)", color: "#e5e7eb", fontSize: 14, fontFamily: "inherit", outline: "none" }} />
        </form>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10 }}>
          {data.digest.theme_signals.map(s => <span key={s} style={pill}>{s}</span>)}
          <div style={{ marginLeft: "auto", display: "flex", background: "rgba(255,255,255,0.04)", borderRadius: 10, padding: 3, border: "1px solid rgba(255,255,255,0.06)" }}>
            {VIEWS.map(v => (
              <button key={v} onClick={() => setView(v)} style={{ padding: "6px 16px", borderRadius: 8, border: "none", cursor: "pointer", fontSize: 12, fontWeight: 500, fontFamily: "inherit", textTransform: "capitalize", background: view === v ? "linear-gradient(135deg,#3b82f6,#7c3aed)" : "transparent", color: view === v ? "#fff" : "#6b7280", transition: "all 0.2s" }}>{v}</button>
            ))}
            <div style={{ width: 1, background: "rgba(255,255,255,0.1)", margin: "2px 4px" }} />
            <button onClick={() => setView("all")} style={{ padding: "6px 16px", borderRadius: 8, border: "none", cursor: "pointer", fontSize: 12, fontWeight: 500, fontFamily: "inherit", background: view === "all" ? "linear-gradient(135deg,#3b82f6,#7c3aed)" : "transparent", color: view === "all" ? "#fff" : "#6b7280", transition: "all 0.2s" }}>All</button>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: sel ? "1fr 320px" : "1fr", minHeight: "calc(100vh - 140px)" }}>
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 20, overflow: "auto" }}>

          {/* ─── BOARD (Graph) ─── */}
          {show("board") && <section>
            <div style={label}>Board — Semantic Map</div>
            <div style={{ ...card, position: "relative", minHeight: 380, overflow: "hidden" }}>
              <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
                {data.graph.edges.map(e => {
                  const f = POS[e.from], t = POS[e.to];
                  if (!f || !t) return null;
                  return <line key={e.id} x1={`${f[0]}%`} y1={`${f[1]}%`} x2={`${t[0]}%`} y2={`${t[1]}%`} stroke={EDGE_CLR[e.label] || "rgba(255,255,255,0.08)"} strokeWidth={e.label === "mentions" ? 1.5 : 1} strokeDasharray={e.label === "enriches" ? "4 4" : "none"} />;
                })}
              </svg>
              {data.graph.nodes.map((node, i) => {
                const p = POS[node.id], c = TYPE_CLR[node.type] || "#6b7280", active = selected === node.id;
                return (
                  <motion.div key={node.id} initial={{ opacity: 0, scale: 0.7 }} animate={{ opacity: node.status === "pending" ? 0.5 : 1, scale: 1 }} transition={{ delay: i * 0.07, type: "spring", stiffness: 260, damping: 22 }}
                    onClick={() => toggle(node.id)}
                    style={{ position: "absolute", left: `${p[0]}%`, top: `${p[1]}%`, transform: "translate(-50%,-50%)", width: 180, padding: 12, borderRadius: 12, border: `1px solid ${active ? c : "rgba(255,255,255,0.08)"}`, background: "rgba(17,17,17,0.92)", backdropFilter: "blur(6px)", cursor: "pointer", boxShadow: active ? `0 0 16px ${c}25` : "0 6px 20px rgba(0,0,0,0.25)", transition: "border-color 0.2s, box-shadow 0.2s" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 4 }}>
                      <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: c }}>{node.source}</span>
                      <span style={{ width: 6, height: 6, borderRadius: "50%", background: STAT_CLR[node.status] || "#6b7280" }} />
                    </div>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{node.title}</div>
                    <div style={{ fontSize: 11, color: "#6b7280", marginTop: 2 }}>{node.subtitle}</div>
                  </motion.div>
                );
              })}
            </div>
          </section>}

          {/* ─── DIGEST ─── */}
          {show("digest") && <section>
            <div style={label}>Digest — Summary Feed</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 10 }}>
              {data.digest.entries.sort((a, b) => b.relevance - a.relevance).map((entry, i) => {
                const node = nodeMap[entry.node_id], c = TYPE_CLR[node?.type] || "#6b7280", active = selected === entry.node_id;
                return (
                  <motion.div key={entry.node_id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
                    onClick={() => toggle(entry.node_id)}
                    style={{ ...card, padding: 14, cursor: "pointer", borderColor: active ? c : undefined, opacity: entry.relevance < 0.4 ? 0.5 : 1, transition: "border-color 0.2s, opacity 0.3s" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
                      <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: c }}>{node?.source}</span>
                      <span style={{ fontSize: 10, color: STAT_CLR[node?.status], textTransform: "capitalize" }}>{node?.status}</span>
                      <span style={{ marginLeft: "auto", fontSize: 10, color: "#6b7280" }}>{Math.round(entry.relevance * 100)}%</span>
                    </div>
                    <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{node?.title}</div>
                    <div style={{ fontSize: 12, color: "#9ca3af", lineHeight: 1.5, marginBottom: 8 }}>{entry.summary}</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {entry.signals.map(s => <span key={s.label} style={{ ...pill, fontSize: 11 }}>{s.label}</span>)}
                    </div>
                  </motion.div>
                );
              })}
            </div>
          </section>}

          {/* ─── COMPARE (Matrix) ─── */}
          {show("compare") && <section>
            <div style={label}>Compare — {data.matrix.rubric}</div>
            <div style={{ ...card, padding: 12, overflowX: "auto" }}>
              <div style={{ display: "grid", gridTemplateColumns: `140px repeat(${data.matrix.columns.length}, 1fr)`, gap: 6, minWidth: 500 }}>
                {/* Header */}
                <div />
                {data.matrix.columns.map(col => (
                  <div key={col.key} style={{ padding: "8px 10px", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "#9ca3af" }}>{col.label}</div>
                ))}
                {/* Rows */}
                {data.matrix.rows.map((row, ri) => {
                  const node = nodeMap[row.node_id], active = selected === row.node_id;
                  return [
                    <motion.div key={`l-${row.node_id}`} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: ri * 0.08 }}
                      onClick={() => toggle(row.node_id)}
                      style={{ padding: "10px 12px", borderRadius: 8, border: `1px solid ${active ? "#3b82f6" : "rgba(255,255,255,0.04)"}`, background: "rgba(255,255,255,0.02)", cursor: "pointer", transition: "border-color 0.2s" }}>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>{node?.title}</div>
                      <div style={{ fontSize: 10, color: "#6b7280" }}>{node?.source}</div>
                    </motion.div>,
                    ...data.matrix.columns.map(col => {
                      const cell = row.cells[col.key];
                      const best = cell.rank === 1, worst = cell.rank === data.matrix.rows.length;
                      return (
                        <motion.div key={`${row.node_id}-${col.key}`} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: ri * 0.08 + 0.03 }}
                          style={{ padding: "10px 12px", borderRadius: 8, display: "flex", alignItems: "center", background: best ? "rgba(34,197,94,0.07)" : worst ? "rgba(239,68,68,0.04)" : "rgba(255,255,255,0.02)", border: `1px solid ${best ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)"}` }}>
                          {col.type === "sentiment"
                            ? <Badge sentiment={cell.sentiment}>{cell.display}</Badge>
                            : <span style={{ fontSize: 13, color: best ? "#22c55e" : "#d1d5db" }}>{cell.display}</span>}
                        </motion.div>
                      );
                    })
                  ];
                })}
              </div>
            </div>
          </section>}
        </div>

        {/* ═══ DETAIL PANEL ═══ */}
        <AnimatePresence>
          {sel && (
            <motion.aside key="detail" initial={{ x: 30, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 30, opacity: 0 }} transition={{ type: "spring", stiffness: 300, damping: 28 }}
              style={{ borderLeft: "1px solid rgba(255,255,255,0.06)", background: "rgba(10,10,10,0.8)", backdropFilter: "blur(12px)", padding: 20, overflowY: "auto" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", marginBottom: 16 }}>
                <div>
                  <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: TYPE_CLR[sel.type] || "#6b7280" }}>{sel.source} · {sel.type}</div>
                  <h3 style={{ fontSize: 17, fontWeight: 600, margin: "4px 0 0" }}>{sel.title}</h3>
                  <div style={{ fontSize: 12, color: "#6b7280" }}>{sel.subtitle}</div>
                </div>
                <button onClick={() => setSelected(null)} style={{ background: "rgba(255,255,255,0.06)", border: "none", borderRadius: 6, width: 24, height: 24, cursor: "pointer", color: "#9ca3af", fontSize: 14 }}>×</button>
              </div>
              <div style={{ ...card, padding: 12, marginBottom: 12 }}>
                <div style={{ ...label, marginBottom: 4 }}>Summary</div>
                <p style={{ fontSize: 13, lineHeight: 1.6, color: "#d1d5db", margin: 0 }}>{sel.summary}</p>
              </div>
              <div style={{ ...card, padding: 12, marginBottom: 12 }}>
                <div style={{ ...label, marginBottom: 6 }}>Tags</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                  {sel.tags.map(t => <span key={t} style={pill}>{t}</span>)}
                </div>
              </div>
              {Object.keys(sel.metadata).length > 0 && (
                <div style={{ ...card, padding: 12 }}>
                  <div style={{ ...label, marginBottom: 4 }}>Metadata</div>
                  {Object.entries(sel.metadata).map(([k, v]) => (
                    <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid rgba(255,255,255,0.04)", fontSize: 12 }}>
                      <span style={{ color: "#6b7280" }}>{k.replace(/_/g, " ")}</span>
                      <span style={{ color: "#e5e7eb", fontWeight: 500 }}>{typeof v === "number" ? v.toLocaleString() : String(v)}</span>
                    </div>
                  ))}
                </div>
              )}
            </motion.aside>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}