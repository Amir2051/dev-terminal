-- Manual local-only integration check. Runs in a transaction and rolls back all fixture data.
-- Invoke only against the isolated Dev Terminal local Supabase database:
-- PGPASSWORD=postgres psql 'postgresql://postgres:postgres@127.0.0.1:55322/postgres' -v ON_ERROR_STOP=1 -f tests/local_agent_commands_integration.sql
BEGIN;
SELECT id::text AS id, owner_id::text AS owner_id
FROM public.workspaces ORDER BY created_at DESC LIMIT 1 \gset ws_
SELECT set_config('request.jwt.claim.sub', :'ws_owner_id', true);
INSERT INTO public.workspace_agents(id,workspace_id,owner_id,label,platform,status)
VALUES ('a8a4b2fe-d5c1-48dc-86e3-4c5d945da871', :'ws_id'::uuid, :'ws_owner_id'::uuid, 'rollback-test-agent','linux','online');
INSERT INTO private.agent_credentials(agent_id,token_hash,last_seen_at)
VALUES ('a8a4b2fe-d5c1-48dc-86e3-4c5d945da871',repeat('a',64),now());

-- Exercise the real pairing RPCs. Fixture rows are rolled back at the end of this script.
SET LOCAL ROLE authenticated;
SELECT (public.create_agent_pairing(
    :'ws_id'::uuid, repeat('b',64), 'rollback-pairing-agent', 'linux', now() + interval '10 minutes'
)->>'agent_id') AS id \gset pair_
RESET ROLE;
SET LOCAL ROLE anon;
SELECT (public.redeem_agent_pairing(repeat('b',64),repeat('c',64),'linux')->>'agent_id') AS redeemed_agent_id \gset
DO $$
DECLARE replay_denied boolean := false; auth_result jsonb;
BEGIN
    BEGIN
        PERFORM public.redeem_agent_pairing(repeat('b',64),repeat('d',64),'linux');
    EXCEPTION WHEN SQLSTATE '28000' THEN
        replay_denied := true;
    END;
    IF NOT replay_denied THEN RAISE EXCEPTION 'Pairing code was redeemable more than once'; END IF;
    auth_result := public.authenticate_local_agent(repeat('c',64));
    IF auth_result IS NULL OR auth_result->>'agent_id' IS NULL THEN
        RAISE EXCEPTION 'Newly paired agent could not authenticate';
    END IF;
END $$;
RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT public.revoke_local_agent(:'pair_id'::uuid) AS pairing_agent_revoked;
RESET ROLE;
SET LOCAL ROLE anon;
DO $$
BEGIN
    IF public.authenticate_local_agent(repeat('c',64)) IS NOT NULL THEN
        RAISE EXCEPTION 'Revoked paired agent could still authenticate';
    END IF;
END $$;
RESET ROLE;

-- First prove that an authenticated workspace owner can enqueue and the paired agent can claim/complete.
SET LOCAL ROLE authenticated;
SELECT (public.enqueue_agent_command(:'ws_id'::uuid,'a8a4b2fe-d5c1-48dc-86e3-4c5d945da871','git-status','dev-terminal')->>'id') AS id \gset read_
RESET ROLE;
SET LOCAL ROLE anon;
SELECT public.claim_agent_command(repeat('a',64)) AS first_claim;
SELECT public.complete_agent_command(repeat('a',64), :'read_id'::uuid,'succeeded','rollback-only integration success') AS first_completion;
RESET ROLE;

-- Then prove sensitive tools cannot be claimed until owner/admin approval.
SET LOCAL ROLE authenticated;
SELECT (public.enqueue_agent_command(:'ws_id'::uuid,'a8a4b2fe-d5c1-48dc-86e3-4c5d945da871','pytest','dev-terminal')->>'id') AS id \gset test_
RESET ROLE;

-- A different authenticated identity must not read command history or approve this command.
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub','00000000-0000-4000-8000-000000000099',true);
SELECT set_config('devtest.workspace_id', :'ws_id', true);
SELECT set_config('devtest.command_id', :'test_id', true);
DO $$
DECLARE history_denied boolean := false; approval_denied boolean := false;
BEGIN
    BEGIN
        PERFORM public.list_agent_commands(current_setting('devtest.workspace_id')::uuid);
    EXCEPTION WHEN insufficient_privilege THEN
        history_denied := true;
    END;
    BEGIN
        PERFORM public.approve_agent_command(current_setting('devtest.command_id')::uuid,true);
    EXCEPTION WHEN insufficient_privilege THEN
        approval_denied := true;
    END;
    IF NOT history_denied THEN RAISE EXCEPTION 'Non-member read command history'; END IF;
    IF NOT approval_denied THEN RAISE EXCEPTION 'Non-member approved another workspace command'; END IF;
END $$;
SELECT set_config('request.jwt.claim.sub', :'ws_owner_id', true);
RESET ROLE;
SET LOCAL ROLE anon;
DO $$
DECLARE claim_result jsonb;
BEGIN
    claim_result := public.claim_agent_command(repeat('a',64));
    IF claim_result->'command' IS DISTINCT FROM 'null'::jsonb THEN
        RAISE EXCEPTION 'Unapproved pytest command was claimable: %', claim_result;
    END IF;
END $$;
RESET ROLE;
SET LOCAL ROLE authenticated;
SELECT public.approve_agent_command(:'test_id'::uuid,true) AS approval_result;
RESET ROLE;
SET LOCAL ROLE anon;
SELECT public.claim_agent_command(repeat('a',64)) AS second_claim;
SELECT public.complete_agent_command(repeat('a',64), :'test_id'::uuid,'succeeded','rollback-only integration success') AS second_completion;
RESET ROLE;
SELECT count(*) AS command_audit_events
FROM public.workspace_audit_events
WHERE details->>'agent_id'='a8a4b2fe-d5c1-48dc-86e3-4c5d945da871';

-- Revocation must invalidate both heartbeat authentication and command claiming.
SET LOCAL ROLE authenticated;
SELECT public.revoke_local_agent('a8a4b2fe-d5c1-48dc-86e3-4c5d945da871'::uuid) AS revoked;
RESET ROLE;
SET LOCAL ROLE anon;
DO $$
DECLARE auth_result jsonb; claim_result jsonb;
BEGIN
    auth_result := public.authenticate_local_agent(repeat('a',64));
    IF auth_result IS NOT NULL THEN
        RAISE EXCEPTION 'Revoked agent still authenticated: %', auth_result;
    END IF;
    claim_result := public.claim_agent_command(repeat('a',64));
    IF claim_result IS NOT NULL THEN
        RAISE EXCEPTION 'Revoked agent still claimed commands: %', claim_result;
    END IF;
END $$;
RESET ROLE;
ROLLBACK;
