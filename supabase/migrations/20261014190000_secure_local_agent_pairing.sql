-- Secure local-agent pairing foundation. Raw pairing codes and agent credentials are never stored.
create table if not exists private.agent_pairing_codes (
    id uuid primary key default gen_random_uuid(),
    agent_id uuid not null references public.workspace_agents(id) on delete cascade,
    code_hash text not null unique check (char_length(code_hash) = 64),
    created_by uuid not null references auth.users(id) on delete cascade,
    expires_at timestamptz not null,
    consumed_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists private.agent_credentials (
    agent_id uuid primary key references public.workspace_agents(id) on delete cascade,
    token_hash text not null unique check (char_length(token_hash) = 64),
    created_at timestamptz not null default now(),
    last_seen_at timestamptz,
    revoked_at timestamptz
);

revoke all on private.agent_pairing_codes, private.agent_credentials from public, anon, authenticated;

create or replace function public.create_agent_pairing(
    target_workspace_id uuid,
    pairing_code_hash text,
    agent_label text,
    agent_platform text,
    expires_at timestamptz
) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    actor_id uuid := auth.uid();
    new_agent_id uuid;
begin
    if actor_id is null then raise exception 'authentication required' using errcode = '28000'; end if;
    if pairing_code_hash !~ '^[a-f0-9]{64}$' then raise exception 'invalid pairing code hash' using errcode = '22023'; end if;
    if char_length(trim(agent_label)) not between 1 and 120 then raise exception 'invalid agent label' using errcode = '22023'; end if;
    if expires_at <= now() or expires_at > now() + interval '15 minutes' then raise exception 'invalid pairing expiry' using errcode = '22023'; end if;
    if not exists (
        select 1 from public.workspaces w
        where w.id = target_workspace_id and w.owner_id = actor_id
    ) and not private.has_workspace_role(target_workspace_id, array['owner','admin']) then
        raise exception 'workspace access denied' using errcode = '42501';
    end if;
    insert into public.workspace_agents(workspace_id, owner_id, label, platform, status)
    select target_workspace_id, actor_id, trim(agent_label), nullif(trim(agent_platform), ''), 'pending' returning id into new_agent_id;
    insert into private.agent_pairing_codes(agent_id, code_hash, created_by, expires_at)
    values (new_agent_id, pairing_code_hash, actor_id, expires_at);
    return jsonb_build_object('agent_id', new_agent_id, 'expires_at', expires_at);
end;
$$;

create or replace function public.redeem_agent_pairing(pairing_code_hash text, credential_hash text, agent_platform text)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    pairing private.agent_pairing_codes%rowtype;
    agent public.workspace_agents%rowtype;
begin
    if pairing_code_hash !~ '^[a-f0-9]{64}$' or credential_hash !~ '^[a-f0-9]{64}$' then
        raise exception 'invalid credential format' using errcode = '22023';
    end if;
    select * into pairing from private.agent_pairing_codes p
      where p.code_hash = pairing_code_hash and p.consumed_at is null and p.expires_at > now()
      for update;
    if not found then raise exception 'pairing code invalid or expired' using errcode = '28000'; end if;
    update private.agent_pairing_codes set consumed_at = now() where id = pairing.id;
    update public.workspace_agents set status = 'online', platform = coalesce(nullif(trim(agent_platform), ''), platform), last_seen_at = now()
      where id = pairing.agent_id and status = 'pending' returning * into agent;
    if not found then raise exception 'agent is not pairable' using errcode = '28000'; end if;
    insert into private.agent_credentials(agent_id, token_hash, last_seen_at)
      values (agent.id, credential_hash, now());
    return jsonb_build_object('agent_id', agent.id, 'workspace_id', agent.workspace_id, 'label', agent.label);
end;
$$;

create or replace function public.authenticate_local_agent(credential_hash text)
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    result jsonb;
begin
    if credential_hash !~ '^[a-f0-9]{64}$' then return null; end if;
    update private.agent_credentials c set last_seen_at = now()
      from public.workspace_agents a
      where c.agent_id = a.id and c.token_hash = credential_hash
        and c.revoked_at is null and a.revoked_at is null and a.status <> 'revoked';
    if not found then return null; end if;
    select jsonb_build_object('agent_id', a.id, 'workspace_id', a.workspace_id, 'owner_id', a.owner_id, 'label', a.label)
      into result from public.workspace_agents a join private.agent_credentials c on c.agent_id = a.id
      where c.token_hash = credential_hash and c.revoked_at is null and a.revoked_at is null and a.status <> 'revoked';
    update public.workspace_agents set status = 'online', last_seen_at = now() where id = (result->>'agent_id')::uuid;
    return result;
end;
$$;

create or replace function public.revoke_local_agent(target_agent_id uuid)
returns boolean
language plpgsql security definer set search_path = '' as $$
declare
    actor_id uuid := auth.uid();
    target_workspace uuid;
begin
    if actor_id is null then raise exception 'authentication required' using errcode = '28000'; end if;
    select workspace_id into target_workspace from public.workspace_agents where id = target_agent_id;
    if target_workspace is null then return false; end if;
    if not exists (select 1 from public.workspaces w where w.id = target_workspace and w.owner_id = actor_id)
       and not private.has_workspace_role(target_workspace, array['owner','admin']) then
        raise exception 'workspace access denied' using errcode = '42501';
    end if;
    update public.workspace_agents set status = 'revoked', revoked_at = now() where id = target_agent_id;
    update private.agent_credentials set revoked_at = now() where agent_id = target_agent_id and revoked_at is null;
    return true;
end;
$$;

revoke all on function public.create_agent_pairing(uuid,text,text,text,timestamptz) from public, anon;
grant execute on function public.create_agent_pairing(uuid,text,text,text,timestamptz) to authenticated;
revoke all on function public.redeem_agent_pairing(text,text,text) from public, authenticated;
grant execute on function public.redeem_agent_pairing(text,text,text) to anon;
revoke all on function public.authenticate_local_agent(text) from public, authenticated;
grant execute on function public.authenticate_local_agent(text) to anon;
revoke all on function public.revoke_local_agent(uuid) from public, anon;
grant execute on function public.revoke_local_agent(uuid) to authenticated;

notify pgrst, 'reload schema';
