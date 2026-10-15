-- Index referencing columns used for workspace ownership and audit lookups.
create index if not exists workspaces_owner_id_idx
  on public.workspaces(owner_id);
create index if not exists workspace_agents_owner_id_idx
  on public.workspace_agents(owner_id);
create index if not exists workspace_audit_events_actor_user_id_idx
  on public.workspace_audit_events(actor_user_id);
