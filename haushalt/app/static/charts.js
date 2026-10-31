// Kleine SVG-Diagramme ohne Abhängigkeiten: Linien, Kalender-Heatmap, gruppierte Säulen, Anteilsbalken.

const NS = "http://www.w3.org/2000/svg";

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function svg(tag, attrs = {}, parent) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

// ---- Tooltip (eine Instanz für alle Diagramme)

let tipEl;
export function tip(x, y, html) {
  if (!tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "tip";
    document.body.appendChild(tipEl);
  }
  tipEl.innerHTML = html;
  tipEl.style.display = "block";
  const r = tipEl.getBoundingClientRect();
  const left = Math.min(Math.max(8, x - r.width / 2), window.innerWidth - r.width - 8);
  const top = y - r.height - 14 < 8 ? y + 18 : y - r.height - 14;
  tipEl.style.left = `${left}px`;
  tipEl.style.top = `${top}px`;
}
export function hideTip() {
  if (tipEl) tipEl.style.display = "none";
}
document.addEventListener("scroll", hideTip, { passive: true, capture: true });

// ---- Skalen

function niceMax(v) {
  if (v <= 0) return { max: 60, step: 15 };
  const steps = [15, 30, 60, 120, 180, 240, 300, 360, 480, 600, 900, 1200, 1800, 2400, 3000, 3600, 6000, 12000];
  const step = steps.find((s) => v / s <= 4) || Math.ceil(v / 4 / 6000) * 6000;
  return { max: Math.ceil(v / step) * step, step };
}

function countMax(v) {
  if (v <= 0) return { max: 4, step: 1 };
  const steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];
  const step = steps.find((s) => v / s <= 4) || 1000;
  return { max: Math.ceil(v / step) * step, step };
}

export function axisFormat(metric) {
  return metric === "time"
    ? (v) => (v === 0 ? "0" : v < 60 ? `${v} min` : `${+(v / 60).toFixed(1)} h`.replace(".", ","))
    : (v) => String(v);
}

// ---- Liniendiagramm mit Fadenkreuz

export function lineChart(el, { labels, tipLabels, series, metric, fmt }) {
  el.innerHTML = "";
  const W = Math.max(280, el.clientWidth);
  const H = 220;
  const m = { l: 46, r: 12, t: 12, b: 26 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;
  const peak = Math.max(0, ...series.flatMap((s) => s.values));
  const { max, step } = metric === "time" ? niceMax(peak) : countMax(peak);
  const n = labels.length;
  const x = (i) => m.l + (n <= 1 ? iw / 2 : (i * iw) / (n - 1));
  const y = (v) => m.t + ih - (v / max) * ih;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "chart", role: "img" }, el);
  const ax = axisFormat(metric);

  for (let v = 0; v <= max + 1e-9; v += step) {
    svg("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: v === 0 ? "baseline" : "grid" }, root);
    svg("text", { x: m.l - 8, y: y(v) + 4, class: "tick", "text-anchor": "end" }, root).textContent = ax(v);
  }
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 64))));
  labels.forEach((lab, i) => {
    if (i % every && i !== n - 1) return;
    if (i !== n - 1 && n - 1 - i < every && i % every === 0 && i !== 0) return;
    svg("text", { x: x(i), y: H - 6, class: "tick", "text-anchor": i === 0 ? "start" : i === n - 1 ? "end" : "middle" }, root).textContent = lab;
  });

  for (const s of series) {
    const pts = s.values.map((v, i) => [x(i), y(v)]);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
    svg("path", { d: `${d}L${x(n - 1)},${y(0)}L${x(0)},${y(0)}Z`, fill: s.color, "fill-opacity": 0.1, stroke: "none" }, root);
    svg("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, root);
    const last = pts[pts.length - 1];
    if (last) svg("circle", { cx: last[0], cy: last[1], r: 4, fill: s.color, class: "ring" }, root);
  }

  const cross = svg("line", { y1: m.t, y2: m.t + ih, class: "cross", visibility: "hidden" }, root);
  const dots = series.map((s) => svg("circle", { r: 5, fill: s.color, class: "ring", visibility: "hidden" }, root));
  const hit = svg("rect", { x: m.l - 6, y: 0, width: iw + 12, height: H, fill: "transparent" }, root);
  const move = (ev) => {
    const box = root.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(n <= 1 ? 0 : ((px - m.l) / iw) * (n - 1))));
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    cross.setAttribute("visibility", "visible");
    series.forEach((s, k) => {
      dots[k].setAttribute("cx", x(i));
      dots[k].setAttribute("cy", y(s.values[i]));
      dots[k].setAttribute("visibility", "visible");
    });
    const rows = [...series].sort((a, b) => b.values[i] - a.values[i])
      .map((s) => `<div class="tip-row"><i style="background:${s.color}"></i>${esc(s.name)}<b>${fmt(s.values[i])}</b></div>`).join("");
    tip(box.left + (x(i) / W) * box.width, box.top + (m.t / H) * box.height + 10, `<div class="tip-title">${esc(tipLabels[i])}</div>${rows}`);
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    hideTip();
  });
}

// ---- Kalender-Heatmap (Spalten = Wochen ab Montag, Zeilen = Wochentage)

const LEVELS = [0, 15, 30, 60, 90];
const MIX = [22, 42, 62, 82, 100];

export function heatColor(color, minutes) {
  if (!minutes) return "var(--cell-empty)";
  let lvl = 0;
  LEVELS.forEach((t, i) => { if (minutes > t) lvl = i; });
  return `color-mix(in oklab, ${color} ${MIX[lvl]}%, var(--surface))`;
}

export function heatLegend(color) {
  const cells = ["var(--cell-empty)", ...MIX.map((p) => `color-mix(in oklab, ${color} ${p}%, var(--surface))`)]
    .map((c) => `<i style="background:${c}"></i>`).join("");
  return `<span class="heat-legend">weniger ${cells} mehr</span>`;
}

export function heatmap(el, { start, weeks, days, color, today, tipFor }) {
  el.innerHTML = "";
  const W = Math.max(260, el.clientWidth);
  const left = 22;
  const top = 16;
  const cell = Math.max(5, Math.min(16, Math.floor((W - left) / weeks)));
  const gap = cell >= 10 ? 3 : 2;
  const H = top + cell * 7;
  const root = svg("svg", { viewBox: `0 0 ${left + cell * weeks} ${H}`, width: left + cell * weeks, height: H, class: "chart heat" }, el);
  ["Mo", "", "Mi", "", "Fr", "", ""].forEach((d, i) => {
    if (d) svg("text", { x: 0, y: top + i * cell + cell - gap / 2 - 1, class: "tick small" }, root).textContent = d;
  });
  let lastMonth = -1;
  for (let w = 0; w < weeks; w++) {
    const monday = new Date(start.getFullYear(), start.getMonth(), start.getDate() + w * 7);
    if (monday.getMonth() !== lastMonth) {
      const tooClose = w === 0 && new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + 21).getMonth() !== monday.getMonth();
      if (w < weeks - 2 && !tooClose) {
        svg("text", { x: left + w * cell, y: 10, class: "tick small" }, root).textContent = monday.toLocaleDateString("de-DE", { month: "short" }).replace(".", "");
      }
      lastMonth = monday.getMonth();
    }
    for (let d = 0; d < 7; d++) {
      const date = new Date(start.getFullYear(), start.getMonth(), start.getDate() + w * 7 + d);
      if (date > today) continue;
      const key = dayKey(date);
      const info = days.get(key);
      const r = svg("rect", {
        x: left + w * cell, y: top + d * cell, width: cell - gap, height: cell - gap,
        rx: Math.min(3, cell / 4), fill: heatColor(color, info?.minutes || 0), class: "cell",
      }, root);
      r.addEventListener("pointerenter", (ev) => tip(ev.clientX, r.getBoundingClientRect().top, tipFor(date, info)));
      r.addEventListener("pointerdown", (ev) => tip(ev.clientX, r.getBoundingClientRect().top, tipFor(date, info)));
      r.addEventListener("pointerleave", hideTip);
    }
  }
}

// ---- Gruppierte Säulen (z. B. Wochentage × Personen)

export function groupedColumns(el, { groups, series, metric, fmt, tipTitle }) {
  el.innerHTML = "";
  const W = Math.max(280, el.clientWidth);
  const H = 180;
  const m = { l: 46, r: 8, t: 10, b: 24 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;
  const peak = Math.max(0, ...series.flatMap((s) => s.values));
  const { max, step } = metric === "time" ? niceMax(peak) : countMax(peak);
  const y = (v) => m.t + ih - (v / max) * ih;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "chart" }, el);
  const ax = axisFormat(metric);
  for (let v = 0; v <= max + 1e-9; v += step) {
    svg("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: v === 0 ? "baseline" : "grid" }, root);
    svg("text", { x: m.l - 8, y: y(v) + 4, class: "tick", "text-anchor": "end" }, root).textContent = ax(v);
  }
  const band = iw / groups.length;
  const bw = Math.min(24, (band * 0.7 - (series.length - 1) * 2) / series.length);
  const gw = bw * series.length + (series.length - 1) * 2;
  groups.forEach((g, gi) => {
    const gx = m.l + gi * band + (band - gw) / 2;
    svg("text", { x: m.l + gi * band + band / 2, y: H - 6, class: "tick", "text-anchor": "middle" }, root).textContent = g;
    series.forEach((s, si) => {
      const v = s.values[gi];
      const h = Math.max(0, y(0) - y(v));
      const bx = gx + si * (bw + 2);
      if (h > 0) {
        const r = Math.min(4, h, bw / 2);
        const top = y(0) - h;
        svg("path", {
          d: `M${bx},${y(0)}V${top + r}Q${bx},${top} ${bx + r},${top}H${bx + bw - r}Q${bx + bw},${top} ${bx + bw},${top + r}V${y(0)}Z`,
          fill: s.color,
        }, root);
      }
      const hit = svg("rect", { x: bx - 1, y: m.t, width: bw + 2, height: ih, fill: "transparent" }, root);
      const show = (ev) => tip(ev.clientX, root.getBoundingClientRect().top + y(v) * (root.getBoundingClientRect().height / H),
        `<div class="tip-title">${esc(tipTitle(gi))}</div><div class="tip-row"><i style="background:${s.color}"></i>${esc(s.name)}<b>${fmt(v)}</b></div>`);
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("pointerdown", show);
      hit.addEventListener("pointerleave", hideTip);
    });
  });
}

// ---- Anteilsbalken (HTML), Segmente mit 2px Abstand

export function splitBar(parts, { height = 10, labels = false } = {}) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  if (!total) return `<div class="split empty" style="height:${height}px"></div>`;
  return `<div class="split" style="height:${height}px">${parts.filter((p) => p.value > 0).map((p) => {
    const pct = (100 * p.value) / total;
    const inner = labels && pct >= 14 ? `<span>${Math.round(pct)} %</span>` : "";
    return `<div class="seg" style="flex:${p.value};background:${p.color}" data-tip="${esc(p.tip || `${p.name}: ${Math.round(pct)} %`)}">${inner}</div>`;
  }).join("")}</div>`;
}

export function dayKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
