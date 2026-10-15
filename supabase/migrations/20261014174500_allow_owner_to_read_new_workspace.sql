-- Allow the canonical owner to read a newly inserted workspace before the owner-membership trigger's row is visible to SELECT RLS.
-- This does not grant access to any workspace whose owner_id differs from auth.uid().
drop policy if exists "workspaces_select_member" on public.workspaces;
create policy "workspaces_select_member" on public.workspaces
for select to authenticated
using (owner_id = (select auth.uid()) or private.is_workspace_member(id));
