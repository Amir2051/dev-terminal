import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const $ = (id) => document.getElementById(id);
let mode = "signin";
let supabase = null;
let activeSession = null;
let commandRefreshTimers = [];

function showMessage(message, isError = false) {
  const node = $("message");
  node.textContent = message;
  node.classList.remove("hidden", "error");
  if (isError) node.classList.add("error");
}
function clearMessage() {
  $("message").classList.add("hidden");
  $("message").classList.remove("error");
}
function setMode(next) {
  mode = next;
  $("signin-tab").classList.toggle("active", mode === "signin");
  $("signup-tab").classList.toggle("active", mode === "signup");
  $("title").textContent = mode === "signin" ? "Welcome back." : "Create your account.";
  $("subtitle").textContent = mode === "signin"
    ? "Sign in to access your dev-terminal account."
    : "Create an account to get ready for your developer workspace.";
  $("submit").textContent = mode === "signin" ? "Sign in" : "Create account";
  $("password").autocomplete = mode === "signin" ? "current-password" : "new-password";
  clearMessage();
}
async function apiRequest(path, options = {}) {
  if (!activeSession?.access_token) throw new Error("Your session has expired. Sign in again.");
  const response = await fetch(path, {
    ...options,
    headers: {
      Authorization: "Bearer " + activeSession.access_token,
      ...(options.headers || {}),
    },
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Workspace request failed.");
  return data;
}

function renderWorkspaces(workspaces) {
  for (const timer of commandRefreshTimers) clearInterval(timer);
  commandRefreshTimers = [];
  const list = $("workspace-list");
  list.replaceChildren();
  if (!workspaces.length) {
    const empty = document.createElement("p");
    empty.textContent = "No workspaces yet. Create one to get started.";
    list.append(empty);
    return;
  }
  for (const workspace of workspaces) {
    const item = document.createElement("div");
    item.className = "workspace-item";
    const name = document.createElement("strong");
    name.textContent = workspace.name || "Untitled workspace";
    const meta = document.createElement("small");
    meta.textContent = "Role: " + (workspace.owner_id === activeSession?.user?.id ? "Owner" : "Member")
      + (workspace.workspace_type ? " · Type: " + workspace.workspace_type : "");
    const actions = document.createElement("div");
    actions.className = "workspace-actions";
    actions.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;margin-top:10px";
    const pairButton = document.createElement("button");
    pairButton.type = "button";
    pairButton.className = "secondary";
    pairButton.textContent = "Pair Linux computer";
    pairButton.addEventListener("click", async () => {
      pairButton.disabled = true;
      clearMessage();
      try {
        const pairing = await apiRequest("/api/agents/pairing", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workspace_id: workspace.id, label: "Linux computer", platform: "linux" }),
        });
        const oldCode = item.querySelector(".pairing-result");
        if (oldCode) oldCode.remove();
        const result = document.createElement("div");
        result.className = "pairing-result";
        result.style.cssText = "margin-top:12px;padding:12px;border:1px solid var(--line);border-radius:9px";
        const title = document.createElement("strong");
        title.textContent = "One-time pairing code";
        const code = document.createElement("code");
        code.textContent = pairing.pairing_code;
        code.style.cssText = "display:block;overflow-wrap:anywhere;font-size:14px;margin:8px 0";
        const expiry = document.createElement("small");
        expiry.textContent = "Expires " + new Date(pairing.expires_at).toLocaleTimeString() + ". Use once; do not share it.";
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "secondary";
        copy.textContent = "Copy code";
        copy.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(pairing.pairing_code);
            copy.textContent = "Copied";
          } catch {
            showMessage("Clipboard access is unavailable. Select and copy the pairing code manually.", true);
          }
        });
        result.append(title, code, expiry, document.createElement("br"), copy);
        item.append(result);
        showMessage("Pairing code created. It expires in 10 minutes.");
        await loadAgents();
      } catch (error) {
        showMessage(error.message || "Could not create pairing code.", true);
      } finally {
        pairButton.disabled = false;
      }
    });
    actions.append(pairButton);
    const agentList = document.createElement("div");
    agentList.className = "workspace-agents";
    agentList.style.cssText = "display:grid;gap:8px;margin-top:10px";
    let availableAgents = [];
    const loadAgents = async () => {
      agentList.replaceChildren();
      try {
        const response = await apiRequest("/api/workspaces/" + encodeURIComponent(workspace.id) + "/agents");
        const agents = Array.isArray(response.agents) ? response.agents : [];
        availableAgents = agents.filter((agent) => agent.status === "online" && !agent.revoked_at);
        if (!agents.length) {
          const empty = document.createElement("small");
          empty.textContent = "No paired computers yet.";
          agentList.append(empty);
          return;
        }
        const heading = document.createElement("small");
        heading.textContent = "Paired computers";
        agentList.append(heading);
        for (const agent of agents) {
          const row = document.createElement("div");
          row.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:8px";
          const details = document.createElement("small");
          details.textContent = (agent.label || "Local agent") + " · " + (agent.status || "unknown")
            + (agent.last_seen_at ? " · Last seen " + new Date(agent.last_seen_at).toLocaleString() : "");
          row.append(details);
          if (agent.status !== "revoked") {
            const revoke = document.createElement("button");
            revoke.type = "button";
            revoke.className = "secondary";
            revoke.textContent = "Revoke";
            revoke.addEventListener("click", async () => {
              if (!window.confirm("Revoke this computer's access? It will no longer authenticate.")) return;
              revoke.disabled = true;
              try {
                await apiRequest("/api/agents/" + encodeURIComponent(agent.id) + "/revoke", { method: "POST" });
                showMessage("Agent access revoked.");
                await loadAgents();
              } catch (error) {
                showMessage(error.message || "Could not revoke agent.", true);
                revoke.disabled = false;
              }
            });
            row.append(revoke);
          }
          agentList.append(row);
        }
      } catch (error) {
        const message = document.createElement("small");
        message.textContent = error.message || "Could not load paired computers.";
        agentList.append(message);
      }
      refreshAgentOptions();
    };

    const commandPanel = document.createElement("div");
    commandPanel.style.cssText = "display:grid;gap:8px;margin-top:14px;padding:12px;border:1px solid var(--line);border-radius:9px";
    const commandTitle = document.createElement("strong");
    commandTitle.textContent = "Remote tools";
    const agentSelect = document.createElement("select");
    agentSelect.setAttribute("aria-label", "Paired computer");
    const toolSelect = document.createElement("select");
    toolSelect.setAttribute("aria-label", "Allowlisted tool");
    const toolOptions = [
      ["pwd", "Print working directory"],
      ["list-files", "List files"],
      ["git-status", "Git status"],
      ["git-log", "Recent commits"],
      ["git-diff", "Diff summary"],
      ["pytest", "Run pytest (approval required)"],
      ["ruff", "Run Ruff"],
      ["git-fetch", "Git fetch (approval required)"],
      ["git-pull", "Git pull (approval required)"],
    ];
    for (const [value, label] of toolOptions) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      toolSelect.append(option);
    }
    const projectInput = document.createElement("input");
    projectInput.type = "text";
    projectInput.maxLength = 100;
    projectInput.placeholder = "Project folder name (direct child of agent projects root)";
    const queueButton = document.createElement("button");
    queueButton.type = "button";
    queueButton.disabled = true;
    queueButton.textContent = "Queue tool";
    const commandList = document.createElement("div");
    commandList.style.cssText = "display:grid;gap:8px";
    const refreshAgentOptions = () => {
      const selected = agentSelect.value;
      agentSelect.replaceChildren();
      for (const agent of availableAgents) {
        const option = document.createElement("option");
        option.value = agent.id;
        option.textContent = agent.label || agent.id;
        agentSelect.append(option);
      }
      if (selected && availableAgents.some((agent) => agent.id === selected)) agentSelect.value = selected;
      queueButton.disabled = availableAgents.length === 0;
    };
    const updateProjectInput = () => {
      const needsProject = toolSelect.value !== "pwd";
      projectInput.classList.toggle("hidden", !needsProject);
      projectInput.disabled = !needsProject;
    };
    toolSelect.addEventListener("change", updateProjectInput);
    updateProjectInput();
    queueButton.addEventListener("click", async () => {
      const toolId = toolSelect.value;
      const projectName = projectInput.value.trim();
      if (!agentSelect.value) return showMessage("Pair a computer and wait for it to come online first.", true);
      if (toolId !== "pwd" && !projectName) return showMessage("Enter the project folder name configured on the paired computer.", true);
      queueButton.disabled = true;
      try {
        const response = await apiRequest("/api/workspaces/" + encodeURIComponent(workspace.id) + "/commands", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            agent_id: agentSelect.value,
            tool_id: toolId,
            project_name: toolId === "pwd" ? null : projectName,
          }),
        });
        showMessage("Tool request queued: " + response.command.status + ".");
        await loadCommands();
      } catch (error) {
        showMessage(error.message || "Could not queue tool.", true);
      } finally {
        queueButton.disabled = availableAgents.length === 0;
      }
    });
    const loadCommands = async () => {
      commandList.replaceChildren();
      try {
        const response = await apiRequest("/api/workspaces/" + encodeURIComponent(workspace.id) + "/commands");
        const commands = Array.isArray(response.commands) ? response.commands : [];
        if (!commands.length) {
          const empty = document.createElement("small");
          empty.textContent = "No tool requests yet.";
          commandList.append(empty);
          return;
        }
        for (const command of commands.slice(0, 20)) {
          const row = document.createElement("div");
          row.style.cssText = "border-top:1px solid var(--line);padding-top:8px;display:grid;gap:6px";
          const summary = document.createElement("small");
          summary.textContent = command.tool_id + (command.project_name ? " · " + command.project_name : "")
            + " · " + command.status + " · " + new Date(command.created_at).toLocaleString();
          row.append(summary);
          if (command.status === "pending_approval") {
            const approvalActions = document.createElement("div");
            approvalActions.style.cssText = "display:flex;gap:8px";
            for (const decision of [{ value: true, label: "Approve" }, { value: false, label: "Reject" }]) {
              const decisionButton = document.createElement("button");
              decisionButton.type = "button";
              decisionButton.className = "secondary";
              decisionButton.textContent = decision.label;
              decisionButton.addEventListener("click", async () => {
                decisionButton.disabled = true;
                try {
                  await apiRequest("/api/commands/" + encodeURIComponent(command.id) + "/approval", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ approve: decision.value }),
                  });
                  showMessage(decision.value ? "Command approved and queued." : "Command rejected.");
                  await loadCommands();
                } catch (error) {
                  showMessage(error.message || "Approval decision failed.", true);
                  decisionButton.disabled = false;
                }
              });
              approvalActions.append(decisionButton);
            }
            row.append(approvalActions);
          }
          if (command.output) {
            const output = document.createElement("pre");
            output.textContent = command.output;
            output.style.cssText = "white-space:pre-wrap;overflow-wrap:anywhere;max-height:180px;overflow:auto;margin:0;font-size:12px";
            row.append(output);
          }
          commandList.append(row);
        }
      } catch (error) {
        const message = document.createElement("small");
        message.textContent = error.message || "Could not load command history.";
        commandList.append(message);
      }
    };
    commandPanel.append(commandTitle, agentSelect, toolSelect, projectInput, queueButton, commandList);
    item.append(name, meta, actions, agentList, commandPanel);
    list.append(item);
    loadAgents();
    loadCommands();
    commandRefreshTimers.push(setInterval(loadCommands, 5000));
  }
}

async function loadWorkspaces() {
  const list = $("workspace-list");
  list.replaceChildren();
  const loading = document.createElement("p");
  loading.textContent = "Loading workspaces…";
  list.append(loading);
  try {
    const data = await apiRequest("/api/workspaces");
    renderWorkspaces(Array.isArray(data.workspaces) ? data.workspaces : []);
  } catch (error) {
    list.replaceChildren();
    const message = document.createElement("p");
    message.textContent = error.message || "Could not load workspaces.";
    list.append(message);
  }
}

async function showSignedIn(session) {
  if (!session?.access_token) return showSignedOut();
  activeSession = session;
  try {
    const response = await fetch("/api/me", {
      headers: { Authorization: "Bearer " + session.access_token },
      cache: "no-store",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Could not verify your session.");
    $("identity").textContent = "Signed in as " + (data.email || data.id || "authenticated user");
    $("auth-panel").classList.add("hidden");
    $("signed-in").classList.remove("hidden");
    $("auth-security-note").classList.add("hidden");
    $("title").textContent = "You're signed in.";
    $("subtitle").textContent = "Your account identity has been verified.";
    clearMessage();
    await loadWorkspaces();
  } catch (error) {
    showMessage(error.message || "Session verification failed.", true);
  }
}
function showSignedOut() {
  for (const timer of commandRefreshTimers) clearInterval(timer);
  commandRefreshTimers = [];
  activeSession = null;
  $("auth-panel").classList.remove("hidden");
  $("signed-in").classList.add("hidden");
  $("auth-security-note").classList.remove("hidden");
  $("title").textContent = "Your workspace starts here.";
  $("subtitle").textContent = "Sign in or create an account to manage your workspaces and paired development computers.";
  setMode(mode);
}
$("signin-tab").addEventListener("click", () => setMode("signin"));
$("signup-tab").addEventListener("click", () => setMode("signup"));
$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!supabase) return showMessage("Authentication is not configured. Ask the administrator to finish setup.", true);
  clearMessage();
  const button = $("submit");
  button.disabled = true;
  try {
    const email = $("email").value.trim();
    const password = $("password").value;
    if (mode === "signup") {
      const { data, error } = await supabase.auth.signUp({ email, password });
      if (error) throw error;
      if (data.session) {
        await showSignedIn(data.session);
      } else {
        showMessage("Account request submitted. Check your email to confirm the account, then sign in.");
      }
    } else {
      const { data, error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) throw error;
      await showSignedIn(data.session);
    }
  } catch (error) {
    showMessage(error.message || "Authentication failed.", true);
  } finally {
    button.disabled = false;
  }
});
$("signout").addEventListener("click", async () => {
  if (!supabase) return;
  const { error } = await supabase.auth.signOut();
  if (error) return showMessage(error.message, true);
  showSignedOut();
});
$("workspace-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("workspace-name");
  const button = $("workspace-create");
  const name = input.value.trim();
  if (!name) return showMessage("Enter a workspace name.", true);
  button.disabled = true;
  clearMessage();
  try {
    await apiRequest("/api/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    input.value = "";
    showMessage("Workspace created.");
    await loadWorkspaces();
  } catch (error) {
    showMessage(error.message || "Could not create workspace.", true);
  } finally {
    button.disabled = false;
  }
});

async function initialize() {
  try {
    const response = await fetch("/api/public-config", { cache: "no-store" });
    const config = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(config.detail || "Could not load account configuration.");
    if (!config.supabase_url || !config.supabase_anon_key) throw new Error("Supabase Auth is not configured on the server.");
    supabase = createClient(config.supabase_url, config.supabase_anon_key, {
      auth: { autoRefreshToken: true, persistSession: true, detectSessionInUrl: true },
    });
    supabase.auth.onAuthStateChange((event, session) => {
      if (session) activeSession = session;
      else if (event === "SIGNED_OUT") activeSession = null;
    });
    const { data, error } = await supabase.auth.getSession();
    if (error) throw error;
    if (data.session) await showSignedIn(data.session);
  } catch (error) {
    showMessage(error.message || "Account service unavailable.", true);
    $("submit").disabled = true;
  }
}
initialize();
