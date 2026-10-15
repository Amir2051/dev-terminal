-- Persist minimal workspace lifecycle audit events in the database transaction.
-- These events are written by a trusted trigger, not by client INSERT privileges.

create or replace function private.audit_workspace_changes()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    event_actor uuid := (select auth.uid());
begin
    if tg_op = 'INSERT' then
        insert into public.workspace_audit_events (workspace_id, actor_user_id, event_type, details)
        values (
            new.id,
            event_actor,
            'workspace.created',
            jsonb_build_object('workspace_type', new.workspace_type)
        );
        return new;
    end if;

    if tg_op = 'UPDATE'
       and (new.name is distinct from old.name or new.workspace_type is distinct from old.workspace_type) then
        insert into public.workspace_audit_events (workspace_id, actor_user_id, event_type, details)
        values (
            new.id,
            event_actor,
            'workspace.updated',
            jsonb_build_object(
                'name_changed', new.name is distinct from old.name,
                'workspace_type_changed', new.workspace_type is distinct from old.workspace_type
            )
        );
    end if;

    return new;
end;
$$;

revoke all on function private.audit_workspace_changes() from public, anon, authenticated;

drop trigger if exists audit_workspace_changes on public.workspaces;
create trigger audit_workspace_changes
after insert or update on public.workspaces
for each row execute function private.audit_workspace_changes();
