-- Prevent moving the canonical owner membership between workspaces.
create or replace function public.guard_workspace_owner_membership()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    old_workspace_owner uuid;
    new_workspace_owner uuid;
begin
    select w.owner_id into new_workspace_owner
    from public.workspaces w
    where w.id = new.workspace_id;

    if new.role = 'owner' and new.user_id is distinct from new_workspace_owner then
        raise exception 'Only the canonical workspace owner may have the owner role.';
    end if;

    if tg_op = 'UPDATE' then
        select w.owner_id into old_workspace_owner
        from public.workspaces w
        where w.id = old.workspace_id;

        if old.user_id = old_workspace_owner
           and (
               new.workspace_id is distinct from old.workspace_id
               or new.user_id is distinct from old.user_id
               or new.role is distinct from 'owner'
           ) then
            raise exception 'The canonical workspace owner membership cannot be changed directly.';
        end if;
    end if;

    return new;
end;
$$;

revoke all on function public.guard_workspace_owner_membership() from public, anon, authenticated;

-- Billing provider identifiers should not be visible to ordinary workspace members.
drop policy if exists workspace_subscriptions_select_member on public.workspace_subscriptions;
create policy workspace_subscriptions_select_admin
on public.workspace_subscriptions
for select to authenticated
using (private.has_workspace_role(workspace_id, array['owner', 'admin']));
