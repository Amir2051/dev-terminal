(() => {
  const state = { tab: "local", projects: [], repos: [], tools: [], selected: null };
  const $ = id => document.getElementById(id);
  let toastTimer;
  function toast(message) {
    const node = $("toast"); node.textContent = message; node.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove("show"), 3600);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", ...options, headers: {
      ...(options.headers || {}), ...(options.body ? { "Content-Type": "application/json", "X-Dev-Terminal-Request": "1" } : {})
    }});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Request failed (" + response.status + ")");
    return data;
  }
  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" })[c]);
  }
  function filteredItems() {
    const q = $("repo-filter").value.trim().toLowerCase();
    const items = state.tab === "local" ? state.projects : state.repos;
    return items.filter(item => [item.name,item.full_name,item.branch,item.description].filter(Boolean).join(" ").toLowerCase().includes(q));
  }
  function renderRepos() {
    const root = $("repo-list"), items = filteredItems();
    if (!items.length) {
      root.innerHTML = '<div class="empty-state">' + (state.tab === "local" ? "No cloned repositories found under PROJECTS_DIR." : "No GitHub repositories returned. Check GITHUB_TOKEN in .env.") + "</div>";
      return;
    }
    root.innerHTML = items.map(item => {
      const local = state.tab === "local", selected = local && state.selected === item.name;
      const badge = local ? (item.dirty ? '<span class="badge dirty">DIRTY</span>' : '<span class="badge clean">CLEAN</span>') : '<span class="badge github">GITHUB</span>';
      const branch = local ? '<span class="repo-branch">' + esc(item.branch || "unknown branch") + "</span>" : '<span class="repo-branch">' + esc(item.private ? "private" : "public") + "</span>";
      const counts = local && item.ahead !== null && item.ahead !== undefined ? "<span>↑" + item.ahead + " ↓" + item.behind + "</span>" : "";
      return '<button class="repo-item ' + (selected ? "selected" : "") + '" data-name="' + esc(item.name) + '" data-local="' + local + '"><span class="repo-name"><span>' + esc(local ? item.name : item.full_name || item.name) + "</span>" + badge + '</span><span class="repo-sub">' + branch + counts + "</span></button>";
    }).join("");
    root.querySelectorAll(".repo-item").forEach(button => button.addEventListener("click", () => {
      if (button.dataset.local === "true") {
        state.selected = button.dataset.name; renderRepos(); renderSelection(); renderTools();
      } else {
        const repo = state.repos.find(item => item.name === button.dataset.name);
        if (repo) toast("GitHub repo: " + repo.full_name + " · clone it from the terminal when ready.");
      }
    }));
  }
  function renderSelection() {
    const project = state.projects.find(item => item.name === state.selected);
    $("selected-project").innerHTML = project ? "<strong>" + esc(project.name) + "</strong> <span>· " + esc(project.path) + "</span>" : "No repository selected";
    $("welcome-title").textContent = project ? project.name : "Select a repository";
    $("welcome-copy").textContent = project ? project.branch + " · " + (project.dirty ? "working tree has changes" : "working tree clean") + " · ahead " + (project.ahead ?? "—") + " / behind " + (project.behind ?? "—") : "Your local workspace, Git status, and tools in one place.";
  }
  function renderTools() {
    $("tool-grid").innerHTML = state.tools.map(tool => '<button class="tool-card" data-tool="' + esc(tool.id) + '"' + (tool.needs_project && !state.selected ? " disabled" : "") + '><strong>' + esc(tool.label) + "</strong><p>" + esc(tool.description) + "</p>" + (tool.write ? '<span class="write-label">⚠ CONFIRM REQUIRED</span>' : "") + "</button>").join("");
    $("tool-grid").querySelectorAll("[data-tool]").forEach(button => button.addEventListener("click", () => runTool(button.dataset.tool)));
  }
  async function runTool(toolId) {
    const tool = state.tools.find(item => item.id === toolId); if (!tool) return;
    if (tool.needs_project && !state.selected) return toast("Select a cloned repository first.");
    let confirmed = false;
    if (tool.write) {
      confirmed = window.confirm("Confirm write-capable action: " + tool.label + "\n\nThis can modify local Git metadata or repository files. Continue?");
      if (!confirmed) return;
    }
    try {
      const result = await api("/api/tools/queue", { method: "POST", body: JSON.stringify({ tool_id: toolId, project: state.selected, confirmed }) });
      toast(result.message || "Command queued for ttyd.");
    } catch (error) { toast(error.message); }
  }
  async function refresh() {
    try {
      const values = await Promise.all([api("/api/projects"), api("/api/repos"), api("/api/tools"), api("/api/health")]);
      const projects = values[0], repos = values[1], tools = values[2], health = values[3];
      state.projects = projects.projects || []; state.repos = repos.repos || []; state.tools = tools.tools || [];
      if (state.selected && !state.projects.some(item => item.name === state.selected)) state.selected = null;
      $("local-count").textContent = state.projects.length; $("github-count").textContent = state.repos.length;
      $("stat-cloned").textContent = state.projects.length; $("stat-dirty").textContent = state.projects.filter(item => item.dirty).length;
      $("stat-github").textContent = state.repos.length; $("health-label").textContent = health.status === "ok" ? "API ONLINE" : "API ERROR";
      renderRepos(); renderSelection(); renderTools();
    } catch (error) { $("health-label").textContent = "API OFFLINE"; toast(error.message); }
  }
  $("repo-filter").addEventListener("input", renderRepos);
  $("refresh-button").addEventListener("click", refresh);
  document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    document.querySelectorAll("[data-tab]").forEach(tab => tab.classList.toggle("active", tab === button));
    renderRepos();
  }));
  if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("/static/sw.js").catch(() => {}));
  refresh(); setInterval(refresh, 30000);
})();
