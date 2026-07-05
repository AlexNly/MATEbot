/* Shot Journal viewer — self-contained, no dependencies. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

let INDEX = null;

const fmtDate = ts => (ts > 1e9 ? new Date(ts * 1000).toLocaleString(undefined, {
  year: "2-digit", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
}) : "—");
const stars = n => n
  ? `<span class="stars">${"★".repeat(n)}<span class="off">${"★".repeat(5 - n)}</span></span>`
  : "";

async function boot() {
  INDEX = await (await fetch("index.json")).json();
  $("#title").textContent = INDEX.title || "Shot Journal";
  document.title = INDEX.title || "Shot Journal";
  $("#search").addEventListener("input", renderList);
  window.addEventListener("hashchange", route);
  renderList();
  route();
}

function renderList() {
  const q = $("#search").value.toLowerCase();
  const rows = INDEX.shots
    .filter(s => !q || `${s.bean} ${s.profile}`.toLowerCase().includes(q))
    .map(s => `<tr data-id="${s.id}">
      <td class="num">#${parseInt(s.id, 10)}</td>
      <td>${fmtDate(s.ts)}</td>
      <td>${esc(s.profile)}</td>
      <td>${esc(s.bean)}</td>
      <td class="num">${s.ratio ? "1:" + s.ratio : ""}</td>
      <td class="num">${s.duration_s ? s.duration_s.toFixed(0) + "s" : ""}</td>
      <td class="num">${s.peak_bar ? s.peak_bar.toFixed(1) + " bar" : ""}</td>
      <td>${stars(s.rating)}</td>
    </tr>`)
    .join("");
  $("#shot-table tbody").innerHTML = rows || `<tr><td colspan="8">No shots match.</td></tr>`;
  for (const tr of document.querySelectorAll("#shot-table tbody tr[data-id]")) {
    tr.addEventListener("click", () => { location.hash = tr.dataset.id; });
  }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

async function route() {
  const id = location.hash.slice(1);
  if (!id) {
    $("#detail-view").hidden = true;
    $("#list-view").hidden = false;
    return;
  }
  const shot = await (await fetch(`shots/${id}.json`)).json();
  $("#list-view").hidden = true;
  $("#detail-view").hidden = false;  // unhide before rendering: charts measure their container
  renderDetail(id, shot);
  window.scrollTo(0, 0);
}

function renderDetail(id, shot) {
  const h = shot.header, n = shot.notes || {};
  $("#shot-title").textContent = `Shot #${parseInt(id, 10)} — ${h.profile}`;
  const bits = [fmtDate(h.ts), `${h.duration_s.toFixed(0)}s`];
  if (h.final_g) bits.push(`<b>${h.final_g.toFixed(1)} g</b> in the cup`);
  if (n.ratio) bits.push(`<b>1:${esc(n.ratio)}</b>`);
  if (n.rating) bits.push(stars(n.rating));
  $("#shot-meta").innerHTML = bits.join(" · ");

  const charts = $("#charts");
  charts.innerHTML = "";
  const t = shot.series.t;
  const S = (key) => shot.series[key] && shot.series[key].some(v => v !== 0) ? shot.series[key] : null;

  chart(charts, "Pressure (bar)", t, h.phases, [
    { label: "pressure", data: S("cp"), color: css("--s1") },
    { label: "target", data: S("tp"), color: css("--s1"), dash: true },
  ]);
  chart(charts, "Flow (ml/s)", t, h.phases, [
    { label: "pump", data: S("fl"), color: css("--s1") },
    { label: "puck", data: S("pf"), color: css("--s2") },
    { label: "scale", data: S("vf"), color: css("--s3") },
  ]);
  chart(charts, "Temperature (°C)", t, h.phases, [
    { label: "boiler", data: S("ct"), color: css("--s1") },
    { label: "target", data: S("tt"), color: css("--s1"), dash: true },
  ]);
  chart(charts, "Weight (g)", t, h.phases, [
    { label: "scale", data: S("v"), color: css("--s1") },
    { label: "estimated", data: S("ev"), color: css("--s2") },
  ]);

  const dl = [];
  const noteFields = [["Bean", n.beanType], ["Grind", n.grindSetting],
    ["Dose in", n.doseIn && n.doseIn + " g"], ["Dose out", n.doseOut && n.doseOut + " g"],
    ["Balance", n.balanceTaste]];
  for (const [k, v] of noteFields) if (v) dl.push(`<dt>${k}</dt><dd>${esc(v)}</dd>`);
  $("#shot-notes").innerHTML = dl.length || n.notes
    ? `<h3>Shot notes</h3>${dl.length ? `<dl>${dl.join("")}</dl>` : ""}` +
      (n.notes ? `<div class="freetext">“${esc(n.notes)}”</div>` : "")
    : "";
}

/* ---- SVG line chart with crosshair tooltip ---- */

function chart(parent, title, t, phases, seriesIn) {
  const series = seriesIn.filter(s => s.data);
  if (!series.length || !t || t.length < 2) return;

  const card = document.createElement("div");
  card.className = "chart-card";
  card.innerHTML = `<h3>${title}</h3>
    <div class="legend">${series.map(s =>
      `<span><span class="chip${s.dash ? " dash" : ""}" style="background:${s.color};color:${s.color}"></span>${s.label}</span>`).join("")}
    </div>`;
  parent.appendChild(card);

  const W = Math.max(320, Math.min(card.clientWidth - 28, 860)), H = 190;
  const padL = 38, padR = 10, padT = 8, padB = 22;
  const xMax = t[t.length - 1] || 1;
  let yMax = Math.max(...series.flatMap(s => s.data)) * 1.08 || 1;
  let yMin = Math.min(0, ...series.flatMap(s => s.data));
  if (title.startsWith("Temperature")) yMin = Math.floor(Math.min(...series.flatMap(s => s.data)) / 10) * 10;
  const x = v => padL + (v / xMax) * (W - padL - padR);
  const y = v => padT + (1 - (v - yMin) / (yMax - yMin)) * (H - padT - padB);

  const grid = niceTicks(yMin, yMax, 4).map(v =>
    `<line x1="${padL}" y1="${y(v)}" x2="${W - padR}" y2="${y(v)}" stroke="var(--grid)"/>
     <text x="${padL - 6}" y="${y(v) + 3}" text-anchor="end">${v}</text>`).join("");
  const xt = niceTicks(0, xMax, 6).map(v =>
    `<text x="${x(v)}" y="${H - 6}" text-anchor="middle">${v}s</text>`).join("");

  // Labels go on two alternating rows; a label is dropped (line kept) only if
  // it would overlap the previous label on BOTH rows.
  const rowEnds = [-Infinity, -Infinity];
  const phaseMarks = (phases || []).filter(p => p.t > 0.5 && p.t < xMax - 0.5).map(p => {
    const px = x(p.t);
    let label = "";
    const row = rowEnds[0] <= px ? 0 : rowEnds[1] <= px ? 1 : -1;
    if (row >= 0) {
      const name = p.name.length > 14 ? p.name.slice(0, 13) + "…" : p.name;
      label = `<text class="phase-label" x="${px + 3}" y="${padT + 9 + row * 11}">${esc(name)}</text>`;
      rowEnds[row] = px + name.length * 5.2 + 10;
    }
    return `<line x1="${px}" y1="${padT}" x2="${px}" y2="${H - padB}" stroke="var(--axis)" stroke-dasharray="2 3"/>${label}`;
  }).join("");

  const paths = series.map(s => {
    const d = s.data.map((v, i) => `${i ? "L" : "M"}${x(t[i]).toFixed(1)},${y(v).toFixed(1)}`).join("");
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2"
      ${s.dash ? 'stroke-dasharray="5 4" opacity="0.75"' : ""} stroke-linejoin="round"/>`;
  }).join("");

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  svg.innerHTML = `${grid}${xt}
    <line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="var(--axis)"/>
    ${phaseMarks}${paths}
    <line class="cross" x1="0" y1="${padT}" x2="0" y2="${H - padB}" stroke="var(--axis)" visibility="hidden"/>`;
  card.appendChild(svg);

  const tip = document.createElement("div");
  tip.className = "tooltip";
  card.appendChild(tip);
  const cross = svg.querySelector(".cross");

  svg.addEventListener("pointermove", ev => {
    const rect = svg.getBoundingClientRect();
    const px = ev.clientX - rect.left;
    if (px < padL || px > W - padR) return hide();
    const time = ((px - padL) / (W - padL - padR)) * xMax;
    let i = t.findIndex(v => v >= time);
    if (i < 0) i = t.length - 1;
    cross.setAttribute("x1", x(t[i]));
    cross.setAttribute("x2", x(t[i]));
    cross.setAttribute("visibility", "visible");
    tip.innerHTML = `${t[i].toFixed(1)}s<br>` + series.map(s =>
      `<span style="color:${s.color}">●</span> ${s.label} <b>${s.data[i].toFixed(1)}</b>`).join("<br>");
    tip.style.display = "block";
    const left = Math.min(px + 14, W - tip.offsetWidth - 8);
    tip.style.left = `${left}px`;
    tip.style.top = `${ev.clientY - rect.top + 10}px`;
  });
  svg.addEventListener("pointerleave", hide);
  function hide() { tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); }
}

function niceTicks(min, max, count) {
  const span = max - min || 1;
  const step = [1, 2, 2.5, 5, 10].map(s => s * 10 ** Math.floor(Math.log10(span / count)))
    .find(s => span / s <= count + 1) || span;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) {
    ticks.push(+v.toFixed(6));
  }
  return ticks;
}

boot();
