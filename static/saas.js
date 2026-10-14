import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const $ = (id) => document.getElementById(id);
let mode = "signin";
let supabase = null;

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
async function showSignedIn(session) {
  if (!session?.access_token) return showSignedOut();
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
    $("title").textContent = "You're signed in.";
    $("subtitle").textContent = "Your account identity has been verified.";
    clearMessage();
  } catch (error) {
    showMessage(error.message || "Session verification failed.", true);
  }
}
function showSignedOut() {
  $("auth-panel").classList.remove("hidden");
  $("signed-in").classList.add("hidden");
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

async function initialize() {
  try {
    const response = await fetch("/api/public-config", { cache: "no-store" });
    const config = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(config.detail || "Could not load account configuration.");
    if (!config.supabase_url || !config.supabase_anon_key) throw new Error("Supabase Auth is not configured on the server.");
    supabase = createClient(config.supabase_url, config.supabase_anon_key, {
      auth: { autoRefreshToken: true, persistSession: true, detectSessionInUrl: true },
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
