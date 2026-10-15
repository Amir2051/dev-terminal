-- Keep RLS helper functions out of the PostgREST-exposed public schema.
-- Policies already reference these functions by object identity, so moving them
-- preserves policy logic while removing public-schema RPC exposure.

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
grant usage on schema private to authenticated;

alter function public.is_workspace_member(uuid) set schema private;
alter function public.has_workspace_role(uuid, text[]) set schema private;

revoke all on function private.is_workspace_member(uuid) from public, anon, authenticated;
revoke all on function private.has_workspace_role(uuid, text[]) from public, anon, authenticated;

grant execute on function private.is_workspace_member(uuid) to authenticated;
grant execute on function private.has_workspace_role(uuid, text[]) to authenticated;
