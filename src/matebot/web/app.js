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
  const hash = location.hash.slice(1);
  if (!hash) {
    $("#detail-view").hidden = true;
    $("#list-view").hidden = false;
    return;
  }
  const [id, compareId] = hash.split("+");
  const shot = await (await fetch(`shots/${id}.json`)).json();
  let compare = null;
  if (compareId) {
    try { compare = await (await fetch(`shots/${compareId}.json`)).json(); } catch { /* ignore */ }
  }
  $("#list-view").hidden = true;
  $("#detail-view").hidden = false;  // unhide before rendering: charts measure their container
  renderDetail(id, shot, compareId, compare);
  window.scrollTo(0, 0);
}

function renderDetail(id, shot, compareId, compare) {
  const h = shot.header, n = shot.notes || {};
  $("#shot-title").textContent = `Shot #${parseInt(id, 10)} — ${h.profile}`;
  const bits = [fmtDate(h.ts), `${h.duration_s.toFixed(0)}s`];
  if (h.final_g) bits.push(`<b>${h.final_g.toFixed(1)} g</b> in the cup`);
  if (n.ratio) bits.push(`<b>1:${esc(n.ratio)}</b>`);
  if (n.rating) bits.push(stars(n.rating));
  $("#shot-meta").innerHTML = bits.join(" · ");

  // compare selector
  const picker = $("#compare");
  picker.innerHTML = `<option value="">compare with…</option>` + INDEX.shots
    .filter(s => s.id !== id)
    .map(s => `<option value="${s.id}" ${s.id === compareId ? "selected" : ""}>#${parseInt(s.id, 10)} · ${esc(s.profile)}${s.bean ? " · " + esc(s.bean) : ""}${s.rating ? " · " + "★".repeat(s.rating) : ""}</option>`)
    .join("");
  picker.onchange = () => { location.hash = picker.value ? `${id}+${picker.value}` : id; };

  const charts = $("#charts");
  charts.innerHTML = "";
  VIDEO = null;
  if (shot.video) {
    const holder = document.createElement("div");
    holder.className = "chart-card video-card";
    holder.innerHTML = `<video src="${esc(shot.video)}" playsinline muted preload="metadata"></video>
      <button class="pbtn vunmute" title="Sound">🔇</button>`;
    charts.appendChild(holder);
    VIDEO = holder.querySelector("video");
    VIDEO_OFFSET = shot.video_offset || 0;
    const mute = holder.querySelector(".vunmute");
    mute.addEventListener("click", () => {
      VIDEO.muted = !VIDEO.muted;
      mute.textContent = VIDEO.muted ? "🔇" : "🔊";
    });
  }
  const t = shot.series.t;
  const S = (key) => shot.series[key] && shot.series[key].some(v => v !== 0) ? shot.series[key] : null;

  let overlay = null;
  if (compare) {
    const cs = compare.series;
    const O = k => cs[k] && cs[k].some(v => v !== 0) ? cs[k] : null;
    overlay = { label: `#${parseInt(compareId, 10)}`, t: cs.t, S: O };
  }

  combinedChart(charts, t, h.phases, S, overlay);

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

/* ---- Shot chart, matching the GaggiMate web UI ----
 * Visual design recreated from scratch to match the machine's own shot chart
 * (GaggiMate's UI code is CC BY-NC-SA and was not copied; colors, axes and
 * annotation styling are reimplemented from observation). Rendered with
 * Chart.js + chartjs-plugin-annotation (both MIT, vendored locally).
 */

const GM = {
  temp: "#F0561D", targetTemp: "#731F00",
  press: "#0066CC", flow: "#63993D", puck: "#204D00",
  weight: "#8B5CF6", weightFlow: "#4b2e8d",
  phase: "#6B7280",
};

function rgba(hex, alpha) {
  const v = parseInt(hex.slice(1, 7), 16);
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${alpha})`;
}

let CHART = null;
let VIDEO = null;
let VIDEO_OFFSET = 0;

function videoSeek(time) {
  if (!VIDEO) return;
  const vt = time + VIDEO_OFFSET;
  if (VIDEO.readyState >= 1 && Math.abs(VIDEO.currentTime - vt) > 0.25) {
    VIDEO.currentTime = Math.max(0, vt);
  }
}

function combinedChart(parent, t, phases, S, overlay) {
  if (!t || t.length < 2 || typeof Chart === "undefined") return;
  Chart.register(window["chartjs-plugin-annotation"]);

  const card = document.createElement("div");
  card.className = "chart-card chartjs-card";
  card.innerHTML = `<div class="chart-holder"><canvas></canvas></div>`;
  parent.appendChild(card);

  const pts = (data, tt = t) => data.map((y, i) => ({ x: tt[i], y }));
  const ds = (label, data, color, opts = {}) => ({
    label, data: pts(data), borderColor: color, backgroundColor: rgba(color, 0.06),
    pointStyle: false, borderWidth: 3, ...opts,
  });

  const datasets = [
    S("ct") && ds("Current Temperature", S("ct"), GM.temp, { yAxisID: "y" }),
    S("tt") && ds("Target Temperature", S("tt"), GM.targetTemp,
      { yAxisID: "y", borderDash: [6, 6], fill: true }),
    S("cp") && ds("Current Pressure", S("cp"), GM.press, { yAxisID: "y1" }),
    S("tp") && ds("Target Pressure", S("tp"), GM.press,
      { yAxisID: "y1", borderDash: [6, 6], fill: true }),
    S("fl") && ds("Current Pump Flow", S("fl"), GM.flow, { yAxisID: "y1" }),
    S("pf") && ds("Current Puck Flow", S("pf"), GM.puck, { yAxisID: "y1" }),
    S("tf") && ds("Target Pump Flow", S("tf"), GM.flow, { yAxisID: "y1", borderDash: [6, 6] }),
    S("v") && ds("Weight", S("v"), GM.weight, { yAxisID: "y2" }),
    S("vf") && ds("Weight Flow", S("vf"), GM.weightFlow, { yAxisID: "y1" }),
  ].filter(Boolean);

  // comparison shot: same series, ghosted (dimmed + thinner), out of the legend
  if (overlay) {
    const O = overlay.S;
    const ghost = (label, data, color, extra = {}) => data && {
      label: `${overlay.label} ${label}`, data: pts(data, overlay.t),
      borderColor: rgba(color, 0.42), pointStyle: false, borderWidth: 2,
      _ghost: true, ...extra,
    };
    datasets.push(...[
      ghost("Temp", O("ct"), GM.temp, { yAxisID: "y" }),
      ghost("Pressure", O("cp"), GM.press, { yAxisID: "y1" }),
      ghost("Pump Flow", O("fl"), GM.flow, { yAxisID: "y1" }),
      ghost("Weight", O("v") || O("ev"), GM.weight, { yAxisID: "y2" }),
    ].filter(Boolean));
  }

  // y (temperature) range with the machine's padding rule
  const temps = datasets.filter(d => d.yAxisID === "y").flatMap(d => d.data.map(p => p.y));
  const tMin = Math.floor(Math.min(...temps)), tMax = Math.ceil(Math.max(...temps));
  const pad = tMax - tMin > 10 ? 2 : 5;

  const hasWeight = datasets.some(d => d.yAxisID === "y2");
  const xMax = Math.max(t[t.length - 1], overlay ? overlay.t[overlay.t.length - 1] : 0);

  const annotations = {};
  (phases || []).forEach((p, i) => {
    if (p.t >= xMax) return;
    annotations[`phase_${i}`] = {
      type: "line", xMin: p.t, xMax: p.t,
      borderColor: GM.phase, borderWidth: 1,
      label: {
        display: true, content: p.name, rotation: -90, position: "end",
        xAdjust: i === 0 ? -5 : -10, padding: { x: 6, y: 0 },
        color: "rgb(255,255,255)", backgroundColor: "rgba(22,33,50,0.75)",
        textAlign: "start", font: { size: 11, weight: 500 }, clip: false,
      },
    };
  });

  const ink2 = css("--ink-2"), grid = css("--grid");
  CHART?.destroy();
  CHART = new Chart(card.querySelector("canvas"), {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, parsing: false,
      spanGaps: true, animation: false, normalized: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          type: "linear", min: t[0], max: xMax,
          title: { display: true, text: "Time (s)", color: ink2 },
          ticks: { color: ink2, font: { size: 12 } }, grid: { color: grid },
        },
        y: {
          min: Math.max(tMin - pad, 0), max: tMax + pad,
          ticks: { color: ink2, callback: v => `${v.toFixed()} °C` },
          grid: { color: grid },
        },
        y1: {
          position: "right", min: 0, max: 16,
          ticks: { color: ink2, callback: v => `${v.toFixed()} bar / g/s` },
          grid: { drawOnChartArea: false },
        },
        ...(hasWeight ? {
          y2: {
            position: "right", min: 0, offset: true,
            // fixed max so the axis doesn't rescale during playback
            max: Math.ceil(Math.max(...datasets.filter(d => d.yAxisID === "y2")
              .flatMap(d => d.data.map(p => p.y))) * 1.05),
            ticks: { color: ink2, callback: v => `${v.toFixed()} g` },
            grid: { drawOnChartArea: false },
          },
        } : {}),
      },
      plugins: {
        tooltip: { filter: item => !CHART?.data.datasets[item.datasetIndex]?._ghost },
        annotation: { annotations },
        legend: {
          position: "top",
          labels: {
            usePointStyle: true, pointStyle: "line", pointStyleWidth: 20,
            padding: 8, color: ink2,
            filter: item => !CHART?.data.datasets[item.datasetIndex]?._ghost,
            generateLabels: chart => {
              const labels = Chart.defaults.plugins.legend.labels.generateLabels(chart)
                .filter(l => !chart.data.datasets[l.datasetIndex]._ghost);
              for (const l of labels) {
                l.lineWidth = 3;
                l.lineDash = chart.data.datasets[l.datasetIndex].borderDash || [];
              }
              return labels;
            },
          },
        },
      },
    },
  });

  attachPlayer(card, t, xMax, S, overlay);
}

/* ---- Shot playback: replay the shot in real time ---- */

const ICON_PLAY = `<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>`;
const ICON_PAUSE = `<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>`;
const ICON_EXPAND = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>`;

function attachPlayer(card, t, xMax, S, overlay) {
  const controls = document.createElement("div");
  controls.className = "player";
  controls.innerHTML = `
    <button class="pbtn" id="play" title="Replay the shot" aria-label="Play">${ICON_PLAY}</button>
    <input type="range" id="scrub" min="0" max="${xMax}" step="0.05" value="${xMax}">
    <span class="ptime" id="ptime">${xMax.toFixed(1)}s</span>
    <select id="speed" title="Playback speed">
      <option value="1">1×</option><option value="2">2×</option>
      <option value="4">4×</option><option value="8">8×</option>
    </select>
    <button class="pbtn" id="theater" title="Full screen" aria-label="Full screen">${ICON_EXPAND}</button>`;
  card.prepend(controls);

  const tiles = document.createElement("div");
  tiles.className = "readouts";
  const TILES = [
    ["cp", "Pressure", "bar", GM.press], ["fl", "Flow", "g/s", GM.flow],
    ["ct", "Temp", "°C", GM.temp], [S("v") ? "v" : "ev", "Weight", "g", GM.weight],
  ].filter(([k]) => S(k));
  tiles.innerHTML = TILES.map(([k, label, unit, color]) =>
    `<div class="tile"><div class="tlabel">${label}</div>
     <div class="tval" id="tile-${k}" style="color:${color}">–<span class="tunit"> ${unit}</span></div></div>`
  ).join("");
  controls.after(tiles);

  const full = CHART.data.datasets.map(d => d.data);
  const times = CHART.data.datasets.map(d => d.data.map(p => p.x));
  CHART.options.plugins.annotation.annotations.playhead = {
    type: "line", xMin: xMax, xMax: xMax, borderColor: "rgba(148,163,184,0.9)",
    borderWidth: 1.5, display: false,
  };

  const idxAt = time => {
    let i = t.length - 1;
    while (i > 0 && t[i] > time) i--;
    return i;
  };

  function renderAt(time, playing) {
    CHART.data.datasets.forEach((d, di) => {
      let n = times[di].length;
      if (time < xMax) {
        n = 0;
        while (n < times[di].length && times[di][n] <= time) n++;
      }
      d.data = full[di].slice(0, Math.max(n, 1));
    });
    const ph = CHART.options.plugins.annotation.annotations.playhead;
    ph.display = playing || time < xMax;
    ph.xMin = ph.xMax = time;
    CHART.update("none");
    if (!playing) videoSeek(time);
    const i = idxAt(time);
    for (const [k] of TILES) {
      const el = $(`#tile-${k}`);
      const unit = el.querySelector(".tunit").outerHTML;
      el.innerHTML = (S(k)[Math.min(i, S(k).length - 1)] ?? 0).toFixed(1) + unit;
    }
    $("#ptime").textContent = `${Math.min(time, xMax).toFixed(1)}s`;
    $("#scrub").value = Math.min(time, xMax);
  }

  let raf = null, playT = xMax, lastTs = null;
  const playBtn = $("#play");

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = null; lastTs = null;
    playBtn.innerHTML = ICON_PLAY;
    VIDEO?.pause();
  }

  function tick(ts) {
    if (lastTs != null) {
      playT += ((ts - lastTs) / 1000) * Number($("#speed").value);
      if (playT >= xMax) {
        playT = xMax;
        renderAt(playT, false);
        stop();
        return;
      }
      renderAt(playT, true);
      videoSeek(playT);
    }
    lastTs = ts;
    raf = requestAnimationFrame(tick);
  }

  playBtn.addEventListener("click", () => {
    if (raf) { stop(); return; }
    if (playT >= xMax) playT = 0;
    playBtn.innerHTML = ICON_PAUSE;
    if (VIDEO) {
      VIDEO.playbackRate = Number($("#speed").value);
      videoSeek(playT);
      VIDEO.play().catch(() => {});
    }
    raf = requestAnimationFrame(tick);
  });
  $("#speed").addEventListener("change", () => {
    if (VIDEO) VIDEO.playbackRate = Number($("#speed").value);
  });
  $("#scrub").addEventListener("input", () => {
    stop();
    playT = Number($("#scrub").value);
    renderAt(playT, true);
  });
  $("#theater").addEventListener("click", () => {
    card.classList.toggle("theater");
    document.body.classList.toggle("theater-open");
    CHART.resize();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && card.classList.contains("theater")) {
      card.classList.remove("theater");
      document.body.classList.remove("theater-open");
      CHART.resize();
    }
  });
  renderAt(xMax, false);
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
