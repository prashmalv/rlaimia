/* ─────────────────────────────────────────────────────────────────────────
   Mia Campaign Engine — Dashboard JavaScript
───────────────────────────────────────────────────────────────────────── */

const API = "";   // Same-origin — no prefix needed
let _bulkUrlsData = null;
let _debounceTimer = null;

// ─── Auth ─────────────────────────────────────────────────────────────────────

let _miaRole  = "viewer";   // default until /api/auth/me responds
let _miaToken = "";

function _getAuth() {
  try {
    const stored = localStorage.getItem("mia_auth");
    if (stored) return JSON.parse(stored);
  } catch(e) {}
  return null;
}

async function initAuth() {
  const stored = _getAuth();
  if (stored?.token) {
    _miaToken = stored.token;
    _miaRole  = stored.role || "viewer";
  }

  try {
    const me = await fetch("/api/auth/me", {
      headers: _miaToken ? {"Authorization": `Bearer ${_miaToken}`} : {},
    });
    if (me.status === 401 && me.url && !me.url.includes("/login")) {
      // auth enabled and token invalid — check if auth is actually required
      const data = await me.json().catch(() => ({}));
      if (data.error) {
        // If there's an auth error but we don't have ADMIN_PASSWORD set, still allow
        const authCheck = await fetch("/api/auth/me");
        if (authCheck.status === 401) {
          localStorage.removeItem("mia_auth");
          window.location.href = "/login";
          return;
        }
      }
    } else if (me.ok) {
      const data = await me.json();
      _miaRole = data.role;
      if (!data.auth_enabled) {
        // Auth disabled on server — no login needed
      }
    }
  } catch(e) {
    // Network error — continue with stored role
  }

  _applyRoleUI();
}

function _applyRoleUI() {
  // Show/hide elements based on role
  const userEl = document.getElementById("sidebarUser");
  if (userEl) {
    userEl.style.display = "flex";
    userEl.style.alignItems = "center";
    const badge = document.getElementById("sidebarRoleBadge");
    if (badge) {
      badge.textContent = _miaRole === "admin" ? "Admin" : "Viewer";
      badge.style.background = _miaRole === "admin"
        ? "rgba(212,175,55,0.15)" : "rgba(156,163,175,0.15)";
      badge.style.color = _miaRole === "admin" ? "var(--mia-gold)" : "#9CA3AF";
    }
  }
  // Show admin-only elements
  document.querySelectorAll("[data-admin-only]").forEach(el => {
    el.style.display = _miaRole === "admin" ? "" : "none";
  });
}

function doLogout() {
  localStorage.removeItem("mia_auth");
  window.location.href = "/login";
}

// Initialise auth on every page load
document.addEventListener("DOMContentLoaded", () => {
  initAuth();
});

// ─── Utilities ───────────────────────────────────────────────────────────────

function fmtNumber(n) {
  if (n == null || n === "—") return "—";
  return parseInt(n).toLocaleString("en-IN");
}

function fmtBytes(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

function _parseUTC(iso) {
  // DB stores UTC without 'Z' suffix — append it so browser parses as UTC, not local time
  if (!iso) return null;
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
}

const _IST_OPTS = { timeZone: "Asia/Kolkata" };

function fmtDate(iso) {
  const d = _parseUTC(iso);
  if (!d) return "—";
  return d.toLocaleString("en-IN", { ..._IST_OPTS, dateStyle: "medium", timeStyle: "short" });
}

function statusBadge(status) {
  const s = (status || "").toLowerCase();
  return `<span class="badge badge-${s}">${s}</span>`;
}

function progressBar(pct) {
  const p = parseFloat(pct) || 0;
  return `
    <div class="progress-bar-wrap">
      <div class="progress-bar">
        <div class="progress-fill" style="width:${p}%"></div>
      </div>
      <span class="progress-label">${p}%</span>
    </div>`;
}

async function apiFetch(path, opts = {}) {
  opts.headers = opts.headers || {};
  if (_miaToken) opts.headers["Authorization"] = `Bearer ${_miaToken}`;
  if (_miaRole)  opts.headers["X-Mia-Role"]    = _miaRole;
  const resp = await fetch(API + path, opts);
  if (!resp.ok) throw new Error(`API error ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

// ─── Stats ───────────────────────────────────────────────────────────────────

async function loadStats() {
  try {
    const data = await apiFetch("/api/stats");
    document.getElementById("totalCampaigns").textContent  = fmtNumber(data.total_campaigns);
    document.getElementById("imagesGenerated").textContent = fmtNumber(data.images_generated);
    document.getElementById("videosGenerated").textContent = fmtNumber(data.videos_generated);
    document.getElementById("overallProgress").textContent = data.overall_progress_pct + "%";
    // Show AI Avatar stat card only when there are any avatar videos
    if (data.ai_avatar_generated > 0) {
      const card = document.getElementById("aiAvatarCard");
      if (card) card.style.display = "";
      const el = document.getElementById("avatarGenerated");
      if (el) el.textContent = fmtNumber(data.ai_avatar_generated);
    }
  } catch (e) {
    console.warn("Stats load failed:", e);
  }
}

// ─── Campaigns Table ──────────────────────────────────────────────────────────

async function loadCampaigns() {
  try {
    const data = await apiFetch("/api/jobs/campaign?limit=50");
    const tbody = document.getElementById("campaignsTbody");
    if (!tbody) return;

    if (!data.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="loading-row">No campaigns yet. Create one to get started.</td></tr>`;
      return;
    }

    tbody.innerHTML = data.map(c => `
      <tr>
        <td><strong>${c.name}</strong></td>
        <td><span style="text-transform:capitalize">${c.event_type}</span></td>
        <td>${statusBadge(c.status)}</td>
        <td>${progressBar(c.progress_pct)}</td>
        <td>${fmtNumber(c.completed_jobs)} / ${fmtNumber(c.total_jobs)}</td>
        <td>${fmtNumber(c.completed_jobs)} / ${fmtNumber(c.total_jobs)}</td>
        <td>${fmtDate(c.created_at)}</td>
        <td>
          <div class="actions-cell">
            <button class="btn btn-sm btn-ghost" onclick="openCampaignDrawer('${c.id}', '${escHtml(c.name)}')">View</button>
            ${c.status === 'running' ? `<button class="btn btn-sm btn-ghost" onclick="pauseCampaign('${c.id}')">Pause</button>` : ''}
            ${c.status === 'paused'  ? `<button class="btn btn-sm btn-primary" onclick="resumeCampaign('${c.id}')">Resume</button>` : ''}
          </div>
        </td>
      </tr>
    `).join("");
  } catch (e) {
    console.error("Campaigns load failed:", e);
  }
}

function escHtml(str) {
  return (str || "").replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' })[c]);
}

// ─── Campaign Drawer ──────────────────────────────────────────────────────────

async function openCampaignDrawer(campaignId, campaignName) {
  document.getElementById("drawerCampaignName").textContent = campaignName;
  document.getElementById("drawerBody").innerHTML = "<p style='color:var(--mia-muted)'>Loading...</p>";
  document.getElementById("campaignDrawer").classList.add("open");

  try {
    const [stats, jobsData] = await Promise.all([
      apiFetch(`/api/jobs/campaign/${campaignId}/stats`),
      apiFetch(`/api/jobs/campaign/${campaignId}/jobs?page=1&page_size=20`),
    ]);

    document.getElementById("drawerBody").innerHTML = `
      <div class="stats-grid" style="grid-template-columns:1fr 1fr 1fr; margin-bottom:20px">
        <div class="stat-card">
          <div class="stat-info">
            <div class="stat-value">${fmtNumber(stats.total)}</div>
            <div class="stat-label">Total Jobs</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-info">
            <div class="stat-value" style="color:var(--mia-success)">${fmtNumber(stats.images_done)}</div>
            <div class="stat-label">Images Done</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-info">
            <div class="stat-value" style="color:var(--mia-info)">${fmtNumber(stats.videos_done)}</div>
            <div class="stat-label">Videos Done</div>
          </div>
        </div>
      </div>
      ${progressBar(stats.progress_pct)}
      <h4 style="margin:20px 0 12px; font-size:14px; color:var(--mia-muted)">Recent Jobs</h4>
      <table class="data-table">
        <thead>
          <tr><th>Name</th><th>Persona</th><th>Phase</th><th>Image</th><th>Video</th><th>Preview</th></tr>
        </thead>
        <tbody>
          ${jobsData.items.map(j => `
            <tr>
              <td>${escHtml(j.person_name)}</td>
              <td><span style="font-size:11px;color:var(--mia-muted)">${escHtml(j.persona)}</span></td>
              <td><span class="badge badge-pending">${j.phase || '—'}</span></td>
              <td>${statusBadge(j.image_status)}</td>
              <td>${statusBadge(j.video_status)}</td>
              <td>
                ${j.image_url  ? `<button class="btn btn-sm btn-ghost" onclick='previewFile("image","${escHtml(j.image_url)}","${escHtml(j.person_name)}")'>Image</button>` : ''}
                ${j.video_url  ? `<button class="btn btn-sm btn-ghost" onclick='previewFile("video","${escHtml(j.video_url)}","${escHtml(j.person_name)}")'>Video</button>` : ''}
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
      <div style="margin-top:16px;display:flex;gap:8px">
        <a href="/files?campaign=${campaignId}&type=images" class="btn btn-ghost btn-sm">Browse Images</a>
        <a href="/files?campaign=${campaignId}&type=videos" class="btn btn-ghost btn-sm">Browse Videos</a>
      </div>
    `;
  } catch (e) {
    document.getElementById("drawerBody").innerHTML = `<p style="color:var(--mia-danger)">Failed to load: ${e.message}</p>`;
  }
}

function closeDrawer() {
  document.getElementById("campaignDrawer").classList.remove("open");
}

// ─── Campaign Actions ─────────────────────────────────────────────────────────

async function pauseCampaign(campaignId) {
  await apiFetch(`/api/jobs/campaign/${campaignId}/pause`, { method: "POST" });
  loadCampaigns();
}

async function resumeCampaign(campaignId) {
  await apiFetch(`/api/jobs/campaign/${campaignId}/resume`, { method: "POST" });
  loadCampaigns();
}

// ─── New Campaign Modal ───────────────────────────────────────────────────────

async function openNewCampaignModal() {
  document.getElementById("newCampaignModal").classList.add("open");
  await _populateTemplateDropdown();
}

async function _populateTemplateDropdown() {
  const sel = document.getElementById("imageTemplateSelect");
  if (!sel) return;
  try {
    const templates = await apiFetch("/api/templates");
    sel.innerHTML = templates.map(t =>
      `<option value="${t.id}">${escHtml(t.name)}${t.is_builtin ? " (built-in)" : ""}</option>`
    ).join("");
  } catch(e) {
    sel.innerHTML = '<option value="">Could not load templates</option>';
  }
}

function closeNewCampaignModal() {
  document.getElementById("newCampaignModal").classList.remove("open");
  document.getElementById("newCampaignForm").reset();
  document.getElementById("personFileName").textContent   = "";
  document.getElementById("templateFileName").textContent = "";
  // Reset avatar section visibility (checkbox unchecked by default)
  const sec = document.getElementById("avatarSection");
  if (sec) sec.style.display = "none";
  const chkAvatar = document.getElementById("chkAvatar");
  if (chkAvatar) chkAvatar.checked = false;
}

function toggleAvatarSection(chk) {
  const sec = document.getElementById("avatarSection");
  if (sec) sec.style.display = chk.checked ? "" : "none";
}

function closeModal(e) {
  if (e.target === e.currentTarget) {
    e.currentTarget.classList.remove("open");
  }
}

function updateFileName(input, labelId, dropId) {
  const label = document.getElementById(labelId);
  const drop  = document.getElementById(dropId);
  if (input.files.length > 0) {
    label.textContent = input.files[0].name;
    drop.classList.add("has-file");
  }
}


document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("newCampaignForm");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn  = document.getElementById("submitCampaignBtn");
    const text = document.getElementById("submitBtnText");
    btn.disabled = true;
    text.textContent = "Launching...";

    const fd = new FormData(form);
    // Explicitly send media-type checkbox values (HTML checkboxes don't submit when unchecked)
    fd.set("generate_images", document.getElementById("chkImages")?.checked ? "true" : "false");
    fd.set("generate_videos", document.getElementById("chkVideos")?.checked ? "true" : "false");
    fd.set("generate_avatar", document.getElementById("chkAvatar")?.checked ? "true" : "false");
    // Image template selection
    const imgTmpl = document.getElementById("imageTemplateSelect")?.value;
    if (imgTmpl) fd.set("image_template_id", imgTmpl);
    try {
      const data = await apiFetch("/api/jobs/campaign", { method: "POST", body: fd });
      closeNewCampaignModal();
      loadCampaigns();
      loadStats();
      showToast(`Campaign "${data.name}" launched! ${data.id}`);
    } catch (err) {
      showToast("Error: " + err.message, "error");
    } finally {
      btn.disabled = false;
      text.textContent = "Launch Campaign";
    }
  });
});

// ─── Files Page ───────────────────────────────────────────────────────────────

let _campaignMap = {};   // { id → { name, created_at } }

async function initFilesPage() {
  if (!document.getElementById("fileGrid")) return;
  try {
    const campaigns = await apiFetch("/api/jobs/campaign?limit=100");
    _campaignMap = {};
    const sel = document.getElementById("campaignFilter");
    if (sel) {
      // keep "All Campaigns" option, clear rest
      while (sel.options.length > 1) sel.remove(1);
      campaigns.forEach(c => {
        _campaignMap[c.id] = c;
        const opt    = document.createElement("option");
        opt.value    = c.id;
        const dt     = c.created_at ? _parseUTC(c.created_at).toLocaleDateString("en-IN", { ..._IST_OPTS, day:"2-digit", month:"short" }) : "";
        const status = c.status === "completed" ? "✓" : c.status === "failed" ? "✗" : "…";
        opt.textContent = `${status} ${c.name} (${dt})`;
        sel.appendChild(opt);
      });

      // Pre-fill from URL query param
      const urlParams = new URLSearchParams(window.location.search);
      if (urlParams.get("campaign")) sel.value = urlParams.get("campaign");
      if (urlParams.get("type")) {
        const mt = document.getElementById("mediaTypeFilter");
        if (mt) mt.value = urlParams.get("type");
      }
    }
  } catch (e) {
    console.warn("Could not load campaign list:", e.message);
  }
  loadFiles();
}

async function loadFiles() {
  const mediaType  = document.getElementById("mediaTypeFilter")?.value || "images";
  const campaignId = (document.getElementById("campaignFilter")?.value || "").trim();
  const limit      = document.getElementById("limitFilter")?.value || "100";
  const grid       = document.getElementById("fileGrid");
  const summary    = document.getElementById("fileSummary");
  const bulkBtn    = document.getElementById("bulkDownloadBtn");
  if (!grid) return;

  grid.innerHTML = `<div class="loading-placeholder">Loading ${mediaType}...</div>`;

  try {
    const params = new URLSearchParams({ limit });
    if (campaignId) params.set("campaign_id", campaignId);

    const data = await apiFetch(`/api/files/${mediaType}?${params}`);
    if (summary) summary.textContent = `${data.count} file(s) found`;
    if (bulkBtn) bulkBtn.style.display = (campaignId && data.count > 0) ? "inline-flex" : "none";
    const delBtn = document.getElementById("deleteFilesBtn");
    if (delBtn) delBtn.style.display = (_miaRole === "admin" && campaignId && data.count > 0 && mediaType !== "avatar_videos") ? "inline-flex" : "none";

    if (!data.files.length) {
      grid.innerHTML = `<div class="loading-placeholder">No files found. Generate a campaign first.</div>`;
      return;
    }

    if (campaignId) {
      // Single campaign: flat grid
      const cInfo   = _campaignMap[campaignId];
      const header  = cInfo
        ? `<div class="campaign-files-header"><strong>${escHtml(cInfo.name)}</strong> <span class="text-muted">${escHtml(campaignId.slice(0,8))}…</span></div>`
        : "";
      grid.innerHTML = header + _renderFileCards(data.files, mediaType);
    } else {
      // All campaigns: group by campaign_id (first path segment)
      const groups = {};
      const order  = [];
      data.files.forEach(f => {
        const cid = f.name.split("/")[0] || "unknown";
        if (!groups[cid]) { groups[cid] = []; order.push(cid); }
        groups[cid].push(f);
      });

      grid.innerHTML = order.map(cid => {
        const cInfo  = _campaignMap[cid];
        const dt     = cInfo?.created_at ? _parseUTC(cInfo.created_at).toLocaleString("en-IN", { ..._IST_OPTS, day:"2-digit", month:"short", hour:"2-digit", minute:"2-digit" }) : "";
        const label  = cInfo ? `${escHtml(cInfo.name)} <span class="text-muted text-xs">${dt} · ${cid.slice(0,8)}…</span>` : `<span class="text-muted">${cid.slice(0,8)}…</span>`;
        const status = cInfo?.status === "completed" ? "badge-success" : cInfo?.status === "failed" ? "badge-danger" : "badge-warning";
        const badge  = cInfo ? `<span class="badge ${status}">${cInfo.status}</span>` : "";
        const delBtn = _miaRole === "admin"
          ? `<button class="btn btn-sm" style="margin-left:auto;color:var(--mia-danger);background:rgba(239,68,68,0.08);border:none;padding:3px 10px;border-radius:6px;font-size:11px;cursor:pointer" onclick="event.stopPropagation();deleteCampaignFiles('${cid}','${mediaType}')">Delete</button>`
          : "";
        return `
          <div class="campaign-files-section">
            <div class="campaign-files-header" style="display:flex;align-items:center;gap:8px">${label} ${badge}
              <span class="text-muted text-xs">${groups[cid].length} file(s)</span>
              ${delBtn}
            </div>
            <div class="file-grid">${_renderFileCards(groups[cid], mediaType)}</div>
          </div>`;
      }).join("");
    }
  } catch (e) {
    grid.innerHTML = `<div class="loading-placeholder" style="color:var(--mia-danger)">Error: ${e.message}</div>`;
  }
}

function _renderFileCards(files, mediaType) {
  return files.map(f => {
    const isVideo = f.name.endsWith(".mp4") || mediaType === "videos" || mediaType === "avatar_videos";
    const fname   = f.name.split("/").pop();
    const thumb   = isVideo
      ? `<div class="file-thumb-placeholder">▶</div>`
      : `<div class="file-thumb"><img src="/api/files/preview/image/${encodeURIComponent(f.name)}" loading="lazy" alt="${fname}" onerror="this.style.display='none'"/></div>`;
    return `
      <div class="file-card" onclick='previewFile("${isVideo ? "video" : "image"}", "${escHtml(f.url)}", "${escHtml(f.name)}")'>
        ${thumb}
        <div class="file-info">
          <div class="file-name-text" title="${escHtml(f.name)}">${escHtml(fname)}</div>
          <div class="file-size">${fmtBytes(f.size)}</div>
        </div>
      </div>`;
  }).join("");
}

function debounceLoadFiles() {
  clearTimeout(_debounceTimer);
  _debounceTimer = setTimeout(loadFiles, 500);
}

async function deleteCampaignFiles(campaignId, mediaType) {
  const label = mediaType === "all" ? "all files" : `${mediaType}`;
  if (!confirm(`Delete ${label} for campaign ${campaignId.slice(0,8)}…?\nThis cannot be undone.`)) return;
  try {
    await apiFetch(`/api/files/campaign/${campaignId}?media_type=${mediaType}`, {
      method: "DELETE",
      headers: {"X-Mia-Role": "admin"},
    });
    showToast(`Deleted ${label} for campaign ${campaignId.slice(0,8)}…`);
    loadFiles();
  } catch(e) {
    showToast("Delete failed: " + e.message, "error");
  }
}

function deleteSelectedCampaignFiles() {
  const campaignId = document.getElementById("campaignFilter")?.value.trim();
  const mediaType  = document.getElementById("mediaTypeFilter")?.value || "images";
  if (!campaignId) { showToast("Select a campaign first", "error"); return; }
  deleteCampaignFiles(campaignId, mediaType);
}

// ─── Preview Modal ────────────────────────────────────────────────────────────

function previewFile(type, url, name) {
  const modal   = document.getElementById("previewModal");
  const body    = document.getElementById("previewBody");
  const title   = document.getElementById("previewTitle");
  const dlBtn   = document.getElementById("previewDownloadBtn");

  title.textContent = name.split("/").pop();
  dlBtn.href        = url;
  dlBtn.download    = name.split("/").pop();

  body.innerHTML = type === "video"
    ? `<video controls autoplay style="max-width:100%;max-height:70vh;border-radius:8px"><source src="${escHtml(url)}" type="video/mp4"/></video>`
    : `<img src="${escHtml(url)}" alt="${escHtml(name)}" style="max-width:100%;max-height:70vh;border-radius:8px"/>`;

  modal.classList.add("open");
}

function closePreviewModal(e) {
  if (!e || e.target === e.currentTarget) {
    document.getElementById("previewModal").classList.remove("open");
    document.getElementById("previewBody").innerHTML = "";
  }
}

// ─── Bulk URLs ────────────────────────────────────────────────────────────────

async function getBulkUrls() {
  const campaignId = document.getElementById("campaignFilter")?.value.trim();
  const mediaType  = document.getElementById("mediaTypeFilter")?.value || "images";
  if (!campaignId) { showToast("Enter a campaign ID first", "error"); return; }

  try {
    const data = await apiFetch(`/api/files/bulk-urls/${campaignId}?media_type=${mediaType}&limit=10000`);
    _bulkUrlsData = data;

    document.getElementById("bulkUrlsInfo").textContent =
      `${data.count} ${mediaType} for campaign ${campaignId} — SAS links valid for ${data.sas_ttl_hrs} hours`;
    document.getElementById("bulkUrlsTextarea").value =
      data.files.map(f => f.url).join("\n");
    document.getElementById("bulkUrlsModal").classList.add("open");
  } catch (e) {
    showToast("Error: " + e.message, "error");
  }
}

function closeBulkUrlsModal(e) {
  if (!e || e.target === e.currentTarget) {
    document.getElementById("bulkUrlsModal").classList.remove("open");
  }
}

function copyBulkUrls() {
  const ta = document.getElementById("bulkUrlsTextarea");
  navigator.clipboard.writeText(ta.value).then(() => showToast("URLs copied!"));
}

function downloadBulkUrlsJson() {
  if (!_bulkUrlsData) return;
  const blob = new Blob([JSON.stringify(_bulkUrlsData, null, 2)], { type: "application/json" });
  const a    = document.createElement("a");
  a.href     = URL.createObjectURL(blob);
  a.download = `bulk-urls-${_bulkUrlsData.campaign_id}.json`;
  a.click();
}

// URL param pre-fill is now handled inside initFilesPage()

// ─── Toast Notifications ──────────────────────────────────────────────────────

function showToast(msg, type = "success") {
  const t = document.createElement("div");
  t.className = "toast toast-" + type;
  t.textContent = msg;
  t.style.cssText = `
    position:fixed; bottom:24px; right:24px; z-index:9999;
    background:${type === "error" ? "var(--mia-danger)" : "var(--mia-success)"};
    color:white; padding:12px 20px; border-radius:8px;
    font-size:13px; max-width:400px; animation:slideIn 0.2s ease;
  `;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}
