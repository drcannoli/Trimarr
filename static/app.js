const statusEl = document.getElementById("status");
const loadingEl = document.getElementById("loading");
const emptyEl = document.getElementById("empty");
const tableWrap = document.getElementById("table-wrap");
const tableBody = document.getElementById("table-body");
const selectAllCheckbox = document.getElementById("select-all");
const selectAllBtn = document.getElementById("select-all-btn");
const dryRunBtn = document.getElementById("dry-run-btn");
const executeBtn = document.getElementById("execute-btn");
const resultToast = document.getElementById("result-toast");

let seriesData = [];

async function api(path, options = {}) {
  const r = await fetch(path, options);
  const data = r.json().catch(() => ({}));
  if (!r.ok) {
    const err = await data;
    throw new Error(err.detail || err.message || `HTTP ${r.status}`);
  }
  return data;
}

function showToast(message, type = "success") {
  resultToast.textContent = message;
  resultToast.className = "toast " + type;
  resultToast.classList.remove("hidden");
  setTimeout(() => resultToast.classList.add("hidden"), 5000);
}

function renderRow(s) {
  const retentionLabel = s.retentionLabel || "";
  const episodesLabel = `${s.episodeFileCount} / ${s.totalEpisodeCount}`;
  const actionLabel = (s.episodesToUnmonitor > 0 || s.filesToDelete > 0)
    ? `${s.episodesToUnmonitor} unmonitor, ${s.filesToDelete} delete`
    : "Nothing to remove";


  const posterCell = s.posterUrl
    ? `<td class="col-poster poster-cell"><img class="poster" src="${escapeHtml(s.posterUrl)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><span class="poster-placeholder" style="display:none">?</span></td>`
    : '<td class="col-poster poster-cell"><span class="poster-placeholder">?</span></td>';

  return `
    <tr data-id="${s.id}">
      <td class="col-check"><input type="checkbox" class="row-check" data-id="${s.id}" ${(s.episodesToUnmonitor > 0 || s.filesToDelete > 0) ? "" : "disabled"}></td>
      ${posterCell}
      <td class="col-title">${escapeHtml(s.title)}</td>
      <td class="col-network">${escapeHtml(s.network || "")}</td>
      <td class="col-quality">${escapeHtml(s.qualityProfile || "")}</td>
      <td class="col-retention"><span class="retention-badge">${escapeHtml(retentionLabel)}</span></td>
      <td class="col-episodes episodes-cell">${episodesLabel}</td>
      <td class="col-action action-cell">${actionLabel}</td>
    </tr>
  `;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

async function loadSeries() {
  loadingEl.classList.remove("hidden");
  emptyEl.classList.add("hidden");
  tableWrap.classList.add("hidden");

  try {
    const data = await api("/api/trimarr-series");
    seriesData = data.series || [];

    if (seriesData.length === 0) {
      loadingEl.classList.add("hidden");
      emptyEl.classList.remove("hidden");
      return;
    }

    tableBody.innerHTML = seriesData.map(renderRow).join("");
    loadingEl.classList.add("hidden");
    tableWrap.classList.remove("hidden");

    tableBody.querySelectorAll(".row-check").forEach(cb => {
      cb.addEventListener("change", updateSelectAllState);
    });
  } catch (e) {
    loadingEl.classList.add("hidden");
    showToast("Error: " + e.message, "error");
  }
}

function updateSelectAllState() {
  const checkboxes = tableBody.querySelectorAll(".row-check:not(:disabled)");
  const checked = Array.from(checkboxes).filter(cb => cb.checked);
  selectAllCheckbox.checked = checkboxes.length > 0 && checked.length === checkboxes.length;
}

function getSelectedIds() {
  return Array.from(tableBody.querySelectorAll(".row-check:checked"))
    .map(cb => parseInt(cb.dataset.id, 10));
}

async function runCleanup(dryRun) {
  const ids = getSelectedIds();
  if (ids.length === 0) {
    showToast("Select at least one series", "error");
    return;
  }

  const btn = dryRun ? dryRunBtn : executeBtn;
  btn.disabled = true;
  try {
    const data = await api("/api/cleanup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ series_ids: ids, dry_run: dryRun }),
    });
    const msg = dryRun
      ? `Would delete ${data.deleted} files, unmonitor ${data.unmonitored} episodes across ${data.series_processed} series.`
      : `Deleted ${data.deleted} files, unmonitored ${data.unmonitored} episodes across ${data.series_processed} series.`;
    showToast(msg);
    if (!dryRun) await loadSeries();
  } catch (e) {
    showToast("Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
  }
}

dryRunBtn.addEventListener("click", () => runCleanup(true));
executeBtn.addEventListener("click", () => runCleanup(false));

selectAllCheckbox.addEventListener("change", (e) => {
  const checked = e.target.checked;
  tableBody.querySelectorAll(".row-check:not(:disabled)").forEach(cb => {
    cb.checked = checked;
  });
});

selectAllBtn.addEventListener("click", () => {
  const checkboxes = tableBody.querySelectorAll(".row-check:not(:disabled)");
  const anyChecked = Array.from(checkboxes).some(cb => cb.checked);
  checkboxes.forEach(cb => { cb.checked = !anyChecked; });
  selectAllCheckbox.checked = !anyChecked;
});

document.querySelectorAll(".nav-item").forEach(el => {
  el.addEventListener("click", (e) => {
    e.preventDefault();
    const tab = el.dataset.tab;
    document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
    el.classList.add("active");
    document.getElementById("series-view").classList.toggle("hidden", tab !== "series");
    document.getElementById("logs-view").classList.toggle("hidden", tab !== "logs");
    if (tab === "logs") loadLogs();
  });
});

async function loadLogs() {
  try {
    const data = await api("/api/logs");
    const content = document.getElementById("logs-content");
    content.innerHTML = (data.logs || [])
      .map(e => `<span class="log-entry ${e.level}">[${e.time}] ${e.level.toUpperCase()}: ${escapeHtml(e.message)}</span>`)
      .join("\n") || "No logs yet.";
  } catch (e) {
    document.getElementById("logs-content").textContent = "Error loading logs: " + e.message;
  }
}

document.getElementById("refresh-logs-btn").addEventListener("click", loadLogs);

(async () => {
  try {
    const data = await api("/api/status");
    statusEl.textContent = data.ok
      ? (data.dry_run ? "Sonarr connected (dry run)" : "Sonarr connected")
      : "Sonarr error";
    statusEl.className = "status " + (data.ok ? "connected" : "error");
  } catch (e) {
    statusEl.textContent = "Connection failed";
    statusEl.className = "status error";
  }
  await loadSeries();
})();
