-- Authenticated, workspace-scoped command queue for paired local agents.
-- Only structured tool IDs are stored; no shell source or arbitrary command strings.
create table if not exists private.agent_commands (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references public.workspaces(id) on delete cascade,
    agent_id uuid not null references public.workspace_agents(id) on delete cascade,
    requested_by uuid not null references auth.users(id) on delete restrict,
    tool_id text not null check (tool_id in (
        'pwd','list-files','git-status','git-log','git-diff','pytest','ruff','git-fetch','git-pull'
    )),
    project_name text,
    status text not null check (status in ('pending_approval','queued','running','succeeded','failed','rejected')),
    output text,
    created_at timestamptz not null default now(),
    approved_by uuid references auth.users(id) on delete set null,
    approved_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    check (
      (tool_id = 'pwd' and project_name is null)
      or (tool_id <> 'pwd' and project_name is not null and char_length(project_name) between 1 and 100
          and project_name !~ '[/\\\\]' and project_name not in ('.','..'))
    ),
    check (output is null or char_length(output) <= 12000)
);
create index if not exists agent_commands_agent_status_created_idx
    on private.agent_commands(agent_id, status, created_at);
revoke all on private.agent_commands from public, anon, authenticated;

create or replace function public.enqueue_agent_command(
    target_workspace_id uuid,
    target_agent_id uuid,
    requested_tool_id text,
    requested_project_name text
) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    actor_id uuid := auth.uid();
    command_id uuid;
    command_status text;
    needs_project boolean;
    active_count integer;
begin
    if actor_id is null then raise exception 'authentication required' using errcode = '28000'; end if;
    if requested_tool_id not in ('pwd','list-files','git-status','git-log','git-diff','pytest','ruff','git-fetch','git-pull') then
        raise exception 'tool not allowed' using errcode = '22023';
    end if;
    needs_project := requested_tool_id <> 'pwd';
    if needs_project and (
      requested_project_name is null or char_length(requested_project_name) not between 1 and 100
      or requested_project_name in ('.','..') or requested_project_name ~ '[/\\\\]'
    ) then raise exception 'invalid project name' using errcode = '22023'; end if;
    if not needs_project and requested_project_name is not null then
      raise exception 'project not required for this tool' using errcode = '22023';
    end if;
    if not exists (
        select 1 from public.workspaces w where w.id = target_workspace_id and w.owner_id = actor_id
    ) and not private.has_workspace_role(target_workspace_id, array['owner','admin']) then
        raise exception 'workspace access denied' using errcode = '42501';
    end if;
    perform 1 from public.workspace_agents a
      where a.id = target_agent_id and a.workspace_id = target_workspace_id
        and a.status = 'online' and a.revoked_at is null
      for update;
    if not found then raise exception 'active paired agent not found in workspace' using errcode = '42501'; end if;
    select count(*) into active_count from private.agent_commands c
      where c.agent_id = target_agent_id and c.status in ('pending_approval','queued','running');
    if active_count >= 20 then raise exception 'agent command queue is full' using errcode = '54000'; end if;
    command_status := case when requested_tool_id in ('git-fetch','git-pull','pytest') then 'pending_approval' else 'queued' end;
    insert into private.agent_commands(workspace_id,agent_id,requested_by,tool_id,project_name,status)
      values (target_workspace_id,target_agent_id,actor_id,requested_tool_id,requested_project_name,command_status)
      returning id into command_id;
    insert into public.workspace_audit_events(workspace_id,actor_user_id,event_type,details)
      values (target_workspace_id,actor_id,'agent_command.requested',
        jsonb_build_object('command_id',command_id,'agent_id',target_agent_id,'tool_id',requested_tool_id,
          'project_name',requested_project_name,'status',command_status));
    return jsonb_build_object('id',command_id,'status',command_status,'tool_id',requested_tool_id);
end;
$$;

create or replace function public.approve_agent_command(target_command_id uuid, approve boolean)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    actor_id uuid := auth.uid();
    cmd private.agent_commands%rowtype;
    next_status text;
begin
    if actor_id is null then raise exception 'authentication required' using errcode = '28000'; end if;
    select * into cmd from private.agent_commands c where c.id = target_command_id for update;
    if not found then raise exception 'command not found' using errcode = 'P0002'; end if;
    if not exists (select 1 from public.workspaces w where w.id = cmd.workspace_id and w.owner_id = actor_id)
       and not private.has_workspace_role(cmd.workspace_id, array['owner','admin']) then
        raise exception 'workspace access denied' using errcode = '42501';
    end if;
    if cmd.status <> 'pending_approval' then raise exception 'command is not awaiting approval' using errcode = '22023'; end if;
    next_status := case when approve then 'queued' else 'rejected' end;
    update private.agent_commands set status = next_status, approved_by = actor_id, approved_at = now()
      where id = target_command_id;
    insert into public.workspace_audit_events(workspace_id,actor_user_id,event_type,details)
      values (cmd.workspace_id,actor_id,case when approve then 'agent_command.approved' else 'agent_command.rejected' end,
        jsonb_build_object('command_id',target_command_id,'agent_id',cmd.agent_id,'tool_id',cmd.tool_id));
    return jsonb_build_object('id',target_command_id,'status',next_status);
end;
$$;

create or replace function public.list_agent_commands(target_workspace_id uuid)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare actor_id uuid := auth.uid(); result jsonb;
begin
    if actor_id is null then raise exception 'authentication required' using errcode = '28000'; end if;
    if not exists (select 1 from public.workspaces w where w.id = target_workspace_id and w.owner_id = actor_id)
       and not private.has_workspace_role(target_workspace_id, array['owner','admin']) then
        raise exception 'workspace access denied' using errcode = '42501';
    end if;
    select coalesce(jsonb_agg(jsonb_build_object(
      'id',c.id,'workspace_id',c.workspace_id,'agent_id',c.agent_id,'requested_by',c.requested_by,
      'tool_id',c.tool_id,'project_name',c.project_name,'status',c.status,'output',c.output,
      'created_at',c.created_at,'approved_by',c.approved_by,'approved_at',c.approved_at,
      'started_at',c.started_at,'completed_at',c.completed_at
    ) order by c.created_at desc),'[]'::jsonb)
    into result from (
      select * from private.agent_commands where workspace_id = target_workspace_id
      order by created_at desc limit 100
    ) c;
    return result;
end;
$$;

create or replace function public.claim_agent_command(credential_hash text)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare agent_row public.workspace_agents%rowtype; cmd private.agent_commands%rowtype;
begin
    if credential_hash !~ '^[a-f0-9]{64}$' then return null; end if;
    select a.* into agent_row from public.workspace_agents a
      join private.agent_credentials c on c.agent_id = a.id
      where c.token_hash = credential_hash and c.revoked_at is null
        and a.revoked_at is null and a.status = 'online'
      for update of a;
    if not found then return null; end if;
    update private.agent_credentials set last_seen_at = now() where agent_id = agent_row.id and revoked_at is null;
    update public.workspace_agents set last_seen_at = now() where id = agent_row.id;
    with expired as (
      update private.agent_commands set status = 'failed', output = 'Agent lease expired before reporting completion.', completed_at = now()
      where agent_id = agent_row.id and status = 'running' and started_at < now() - interval '5 minutes'
      returning workspace_id, requested_by, id, agent_id, tool_id
    )
    insert into public.workspace_audit_events(workspace_id,actor_user_id,event_type,details)
      select workspace_id,requested_by,'agent_command.failed',
        jsonb_build_object('command_id',id,'agent_id',agent_id,'tool_id',tool_id,'reason','agent_lease_expired')
      from expired;
    select * into cmd from private.agent_commands c
      where c.agent_id = agent_row.id and c.status = 'queued'
      order by c.created_at limit 1 for update skip locked;
    if not found then return jsonb_build_object('command',null); end if;
    update private.agent_commands set status = 'running', started_at = now() where id = cmd.id returning * into cmd;
    insert into public.workspace_audit_events(workspace_id,actor_user_id,event_type,details)
      values (cmd.workspace_id,cmd.requested_by,'agent_command.started',
        jsonb_build_object('command_id',cmd.id,'agent_id',cmd.agent_id,'tool_id',cmd.tool_id));
    return jsonb_build_object('command',jsonb_build_object(
      'id',cmd.id,'tool_id',cmd.tool_id,'project_name',cmd.project_name,
      'approved_by',cmd.approved_by,'approved_at',cmd.approved_at
    ));
end;
$$;

create or replace function public.complete_agent_command(
    credential_hash text, target_command_id uuid, outcome text, command_output text
) returns boolean
language plpgsql security definer set search_path = '' as $$
declare agent_id_value uuid; cmd private.agent_commands%rowtype; safe_output text;
begin
    if credential_hash !~ '^[a-f0-9]{64}$' or outcome not in ('succeeded','failed') then return false; end if;
    select a.id into agent_id_value from public.workspace_agents a
      join private.agent_credentials c on c.agent_id = a.id
      where c.token_hash = credential_hash and c.revoked_at is null
        and a.revoked_at is null and a.status = 'online';
    if not found then return false; end if;
    select * into cmd from private.agent_commands c
      where c.id = target_command_id and c.agent_id = agent_id_value and c.status = 'running' for update;
    if not found then return false; end if;
    safe_output := left(coalesce(command_output,''),12000);
    update private.agent_commands set status = outcome, output = safe_output, completed_at = now()
      where id = target_command_id;
    insert into public.workspace_audit_events(workspace_id,actor_user_id,event_type,details)
      values (cmd.workspace_id,cmd.requested_by,'agent_command.' || outcome,
        jsonb_build_object('command_id',cmd.id,'agent_id',cmd.agent_id,'tool_id',cmd.tool_id,
          'output_truncated',char_length(coalesce(command_output,'')) > 12000));
    return true;
end;
$$;

revoke all on function public.enqueue_agent_command(uuid,uuid,text,text) from public, anon;
grant execute on function public.enqueue_agent_command(uuid,uuid,text,text) to authenticated;
revoke all on function public.approve_agent_command(uuid,boolean) from public, anon;
grant execute on function public.approve_agent_command(uuid,boolean) to authenticated;
revoke all on function public.list_agent_commands(uuid) from public, anon;
grant execute on function public.list_agent_commands(uuid) to authenticated;
revoke all on function public.claim_agent_command(text) from public, authenticated;
grant execute on function public.claim_agent_command(text) to anon;
revoke all on function public.complete_agent_command(text,uuid,text,text) from public, authenticated;
grant execute on function public.complete_agent_command(text,uuid,text,text) to anon;
notify pgrst, 'reload schema';
