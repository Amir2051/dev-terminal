-- dev-terminal SaaS foundation.
-- This schema is not yet wired into the FastAPI application. All user-facing tables
-- enable RLS; billing, audit writes, and agent provisioning are server-managed only.

create table if not exists public.profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    display_name text check (display_name is null or char_length(display_name) <= 120),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.workspaces (
    id uuid primary key default gen_random_uuid(),
    name text not null check (char_length(name) between 1 and 120),
    owner_id uuid not null references auth.users(id) on delete restrict,
    workspace_type text not null default 'hybrid' check (workspace_type in ('cloud', 'local-agent', 'hybrid')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.workspace_members (
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    role text not null default 'member' check (role in ('owner', 'admin', 'member')),
    created_at timestamptz not null default now(),
    primary key (workspace_id, user_id)
);

create table if not exists public.workspace_agents (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    label text not null check (char_length(label) between 1 and 120),
    platform text check (platform is null or char_length(platform) <= 80),
    status text not null default 'pending' check (status in ('pending', 'online', 'offline', 'revoked')),
    last_seen_at timestamptz,
    created_at timestamptz not null default now(),
    revoked_at timestamptz
);

create table if not exists public.workspace_subscriptions (
    workspace_id uuid primary key references public.workspaces(id) on delete cascade,
    billing_provider text not null default 'stripe' check (billing_provider in ('stripe')),
    provider_customer_id text unique,
    provider_subscription_id text unique,
    plan_key text not null default 'free',
    status text not null default 'inactive' check (status in ('trialing', 'active', 'past_due', 'canceled', 'inactive')),
    current_period_end timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.workspace_audit_events (
    id bigint generated always as identity primary key,
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    actor_user_id uuid references auth.users(id) on delete set null,
    event_type text not null check (char_length(event_type) between 1 and 120),
    details jsonb not null default '{}'::jsonb check (jsonb_typeof(details) = 'object'),
    created_at timestamptz not null default now()
);

create index if not exists workspace_members_user_idx on public.workspace_members(user_id);
create index if not exists workspace_agents_workspace_idx on public.workspace_agents(workspace_id);
create index if not exists workspace_audit_events_workspace_created_idx
    on public.workspace_audit_events(workspace_id, created_at desc);

-- Security-definer membership checks avoid recursive RLS policy evaluation.
create or replace function public.is_workspace_member(target_workspace_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.workspace_members m
        where m.workspace_id = target_workspace_id
          and m.user_id = (select auth.uid())
    );
$$;

create or replace function public.has_workspace_role(target_workspace_id uuid, allowed_roles text[])
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.workspace_members m
        where m.workspace_id = target_workspace_id
          and m.user_id = (select auth.uid())
          and m.role = any(allowed_roles)
    );
$$;

revoke all on function public.is_workspace_member(uuid) from public, anon;
revoke all on function public.has_workspace_role(uuid, text[]) from public, anon;
grant execute on function public.is_workspace_member(uuid) to authenticated;
grant execute on function public.has_workspace_role(uuid, text[]) to authenticated;

create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.profiles (id, display_name)
    values (new.id, nullif(new.raw_user_meta_data ->> 'display_name', ''))
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created_dev_terminal on auth.users;
create trigger on_auth_user_created_dev_terminal
after insert on auth.users
for each row execute function public.handle_new_auth_user();

create or replace function public.add_workspace_owner_member()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.workspace_members (workspace_id, user_id, role)
    values (new.id, new.owner_id, 'owner')
    on conflict (workspace_id, user_id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_workspace_created_add_owner on public.workspaces;
create trigger on_workspace_created_add_owner
after insert on public.workspaces
for each row execute function public.add_workspace_owner_member();

alter table public.profiles enable row level security;
alter table public.workspaces enable row level security;
alter table public.workspace_members enable row level security;
alter table public.workspace_agents enable row level security;
alter table public.workspace_subscriptions enable row level security;
alter table public.workspace_audit_events enable row level security;

create policy "profiles_select_self" on public.profiles
for select to authenticated using (id = (select auth.uid()));
create policy "profiles_insert_self" on public.profiles
for insert to authenticated with check (id = (select auth.uid()));
create policy "profiles_update_self" on public.profiles
for update to authenticated using (id = (select auth.uid()))
with check (id = (select auth.uid()));

create policy "workspaces_select_member" on public.workspaces
for select to authenticated using (public.is_workspace_member(id));
create policy "workspaces_insert_owner" on public.workspaces
for insert to authenticated with check (owner_id = (select auth.uid()));
create policy "workspaces_update_admin" on public.workspaces
for update to authenticated
using (public.has_workspace_role(id, array['owner', 'admin']))
with check (public.has_workspace_role(id, array['owner', 'admin']));
create policy "workspaces_delete_owner" on public.workspaces
for delete to authenticated using (public.has_workspace_role(id, array['owner']));

create policy "workspace_members_select_member" on public.workspace_members
for select to authenticated
using (user_id = (select auth.uid()) or public.is_workspace_member(workspace_id));
create policy "workspace_members_insert_admin" on public.workspace_members
for insert to authenticated
with check (
    public.has_workspace_role(workspace_id, array['owner'])
    or (role = 'member' and public.has_workspace_role(workspace_id, array['admin']))
);
create policy "workspace_members_update_owner" on public.workspace_members
for update to authenticated
using (public.has_workspace_role(workspace_id, array['owner']))
with check (public.has_workspace_role(workspace_id, array['owner']));
create policy "workspace_members_delete_admin" on public.workspace_members
for delete to authenticated
using (
    public.has_workspace_role(workspace_id, array['owner'])
    or (role = 'member' and public.has_workspace_role(workspace_id, array['admin']))
    or (user_id = (select auth.uid()) and role <> 'owner')
);

create policy "workspace_agents_select_member" on public.workspace_agents
for select to authenticated using (public.is_workspace_member(workspace_id));

create policy "workspace_subscriptions_select_member" on public.workspace_subscriptions
for select to authenticated using (public.is_workspace_member(workspace_id));

create policy "workspace_audit_events_select_member" on public.workspace_audit_events
for select to authenticated using (public.is_workspace_member(workspace_id));

grant select, insert, update on public.profiles to authenticated;
grant select, insert, update, delete on public.workspaces to authenticated;
grant select, insert, update, delete on public.workspace_members to authenticated;
grant select on public.workspace_agents to authenticated;
grant select on public.workspace_subscriptions to authenticated;
grant select on public.workspace_audit_events to authenticated;

-- No client write policies or grants for agent registration, subscription changes,
-- or audit events. Those operations must go through a trusted server-side workflow.
