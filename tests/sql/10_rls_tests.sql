-- Row-level-security and business-rule tests for the self-serve migration.
-- Run by tests/sql/run.sh against a throwaway Postgres. Any failed check aborts the run.
\set ON_ERROR_STOP on
\set QUIET on
\o /dev/null
begin;

create schema tests;
grant usage on schema tests to anon, authenticated, service_role;

create function tests.ok(cond boolean, msg text) returns void language plpgsql as $$
begin
  if cond is not true then raise exception 'FAIL: %', msg; end if;
  raise notice 'ok   - %', msg;
end $$;

-- Passes only if the statement raises an error with the given SQLSTATE (null = any error).
create function tests.throws(stmt text, state text, msg text) returns void language plpgsql as $$
begin
  begin
    execute stmt;
  exception when others then
    if state is null or sqlstate = state then
      raise notice 'ok   - % (%)', msg, sqlstate; return;
    end if;
    raise exception 'FAIL: % (expected %, got % %)', msg, state, sqlstate, sqlerrm;
  end;
  raise exception 'FAIL: % (no error raised)', msg;
end $$;

create function tests.count(stmt text) returns bigint language plpgsql as $$
declare n bigint; begin execute 'select count(*) from (' || stmt || ') q' into n; return n; end $$;

create function tests.as_user(u uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims', json_build_object('sub', u, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
end $$;
create function tests.as_anon() returns void language plpgsql as $$
begin perform set_config('request.jwt.claims', '', true); execute 'set local role anon'; end $$;
create function tests.reset() returns void language plpgsql as $$
begin execute 'reset role'; perform set_config('request.jwt.claims', '', true); end $$;
grant execute on all functions in schema tests to anon, authenticated, service_role;

-- Fixtures: A (with metadata), B (no firm name, bad nation), C (stranger).
insert into auth.users (id, email, raw_user_meta_data) values
  ('aaaaaaaa-0000-0000-0000-000000000001', 'a@strangford.example', '{"firm_name":"Strangford Solicitors","nation":"Northern Ireland"}'),
  ('bbbbbbbb-0000-0000-0000-000000000002', 'b@harbour.example',    '{"nation":"Atlantis"}'),
  ('cccccccc-0000-0000-0000-000000000003', 'c@other.example',      '{}');

------------------------------------------------------------------ sign-up trigger
select tests.ok((select count(*) from public.firms) = 3, 'a firm row is created for each new user');
select tests.ok((select name from public.firms where owner_id = 'aaaaaaaa-0000-0000-0000-000000000001') = 'Strangford Solicitors', 'firm name comes from sign-up metadata');
select tests.ok((select nation from public.firms where owner_id = 'aaaaaaaa-0000-0000-0000-000000000001') = 'Northern Ireland', 'nation comes from sign-up metadata');
select tests.ok((select name from public.firms where owner_id = 'bbbbbbbb-0000-0000-0000-000000000002') = 'b', 'missing firm name falls back to the email local part');
select tests.ok((select nation from public.firms where owner_id = 'bbbbbbbb-0000-0000-0000-000000000002') = 'England', 'invalid nation falls back to England');
select tests.ok((select email from public.firms where owner_id = 'aaaaaaaa-0000-0000-0000-000000000001') = 'a@strangford.example', 'enquiries email is pre-filled from sign-up');
select tests.ok((select not listed from public.firms where owner_id = 'aaaaaaaa-0000-0000-0000-000000000001'), 'new firms start unlisted');

-- Handy ids
create temp table ids as
  select (select id from public.firms where owner_id = 'aaaaaaaa-0000-0000-0000-000000000001') as fa,
         (select id from public.firms where owner_id = 'bbbbbbbb-0000-0000-0000-000000000002') as fb;
grant select on ids to anon, authenticated, service_role;

------------------------------------------------------------------ firms: ownership
select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001');
select tests.ok(tests.count('select 1 from public.firms') = 1, 'a firm can read only its own row');
select tests.ok(tests.count($$select 1 from public.firms where name = 'b'$$) = 0, 'a firm cannot read another firm''s row');
update public.firms set town = 'Newtownards', phone = '028 9181 0000', reg_no = 'NI-12345', about = 'High-street practice.',
  services = '{"will1":{"type":"fixed","fee":195}}' where id = (select fa from ids);
select tests.ok((select town from public.firms) = 'Newtownards', 'a firm can update its own details');
update public.firms set town = 'Hijacked' where id = (select fb from ids);
select tests.reset();
select tests.ok((select town from public.firms where id = (select fb from ids)) = '', 'a firm cannot update another firm''s row (0 rows changed)');

select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001');
select tests.throws($$update public.firms set suspended = true$$, '42501', 'a firm cannot change its own suspended flag');
select tests.throws($$update public.firms set owner_id = 'cccccccc-0000-0000-0000-000000000003'$$, '42501', 'a firm cannot reassign ownership');
select tests.throws($$insert into public.firms (owner_id, name, nation) values ('cccccccc-0000-0000-0000-000000000003','x','England')$$, '42501', 'clients cannot insert firms directly');
select tests.throws($$delete from public.firms$$, '42501', 'clients cannot delete firms directly');
select tests.throws($$update public.firms set color = 'red'$$, '23514', 'colour must be a hex value');
select tests.throws($$update public.firms set services = '[1,2]'$$, '23514', 'services must be a JSON object');
select tests.throws($$update public.firms set about = repeat('x', 301)$$, '23514', 'about is capped at 300 characters');
select tests.throws($$update public.firms set nation = 'Mars'$$, '23514', 'nation must be one of the four');

------------------------------------------------------------------ anon cannot touch base tables
select tests.as_anon();
select tests.throws('select * from public.firms', '42501', 'anon cannot read the firms table');
select tests.throws('select * from public.firm_billing', '42501', 'anon cannot read billing');
select tests.throws('select * from public.enquiries', '42501', 'anon cannot read enquiries');
select tests.throws($$insert into public.enquiries (firm_id, service, name, email) values (gen_random_uuid(),'will1','x','x@x.x')$$, '42501', 'anon cannot insert enquiries directly (the function does it)');
select tests.ok(public.firm_is_premium(gen_random_uuid()) = false, 'the premium check is callable by anon and says no for unknown firms');
select tests.reset();

------------------------------------------------------------------ public listing
select tests.as_anon();
select tests.ok(tests.count('select 1 from public.public_firms') = 0, 'unlisted firms are not public');
select tests.reset();

select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001');
update public.firms set listed = true;
select tests.reset();

select tests.as_anon();
select tests.ok(tests.count('select 1 from public.public_firms') = 1, 'listing a firm makes it public');
select tests.ok((select premium from public.public_firms) = false, 'a free firm is not marked premium');
select tests.throws('select owner_id from public.public_firms', '42703', 'the public view does not expose owner ids');
select tests.throws('select suspended from public.public_firms', '42703', 'the public view does not expose the suspended flag');
select tests.throws($$update public.public_firms set name = 'Defaced'$$, '42501', 'anon cannot write through the public view');
select tests.throws($$delete from public.public_firms$$, '42501', 'anon cannot delete through the public view');
select tests.reset();
select tests.as_user('cccccccc-0000-0000-0000-000000000003');
select tests.throws($$update public.public_firms set name = 'Defaced'$$, '42501', 'signed-in users cannot write through the public view');
select tests.reset();

select tests.as_user('cccccccc-0000-0000-0000-000000000003');
select tests.ok(tests.count('select 1 from public.public_firms') = 1, 'signed-in strangers can also read the public view');
select tests.reset();

-- admin suspension (service role)
select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001'); select tests.reset();
update public.firms set suspended = true where id = (select fa from ids);
select tests.as_anon();
select tests.ok(tests.count('select 1 from public.public_firms') = 0, 'a suspended firm disappears from the public listing');
select tests.reset();
update public.firms set suspended = false where id = (select fa from ids);

------------------------------------------------------------------ premium: logo
select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001');
select tests.throws(format($$update public.firms set logo_path = '%s/logo.png'$$, (select fa from ids)), '42501', 'a free firm cannot set a logo');
select tests.throws($$update public.firms set logo_path = '../../etc/passwd'$$, null, 'a logo path must look like <firm-id>/<file>');
update public.firms set logo_path = null;   -- clearing is always allowed
select tests.ok(true, 'a free firm can always clear a logo');
select tests.throws($$select * from public.firm_billing where stripe_customer_id is not null$$, '42501', 'firms cannot read Stripe ids');
select tests.reset();

-- Webhook (service role) activates the subscription.
select tests.as_anon(); select tests.reset();
set local role service_role;
insert into public.firm_billing (firm_id, stripe_customer_id, stripe_subscription_id, status, current_period_end)
  values ((select fa from ids), 'cus_A', 'sub_A', 'active', now() + interval '30 days');
reset role;

select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001');
select tests.ok((select status from public.firm_billing) = 'active', 'a firm can read its own billing status');
select tests.ok(public.firm_is_premium((select fa from ids)), 'an active subscription is premium');
update public.firms set logo_path = (select fa from ids)::text || '/logo.png';
select tests.ok(true, 'a premium firm can set a logo');
select tests.throws($$update public.firm_billing set status = 'active'$$, '42501', 'firms cannot edit their billing row');
select tests.throws($$insert into public.firm_billing (firm_id, status) values (gen_random_uuid(), 'active')$$, '42501', 'firms cannot insert billing rows');
select tests.reset();

select tests.as_user('bbbbbbbb-0000-0000-0000-000000000002');
select tests.ok(tests.count('select 1 from public.firm_billing') = 0, 'a firm cannot read another firm''s billing');
select tests.reset();

select tests.as_anon();
select tests.ok((select premium from public.public_firms) = true, 'public view marks the firm premium');
select tests.ok((select logo_path from public.public_firms) is not null, 'public view returns the logo for a premium firm');
select tests.reset();

-- Lapse: the logo stops being shown but is kept.
update public.firm_billing set status = 'canceled' where firm_id = (select fa from ids);
select tests.as_anon();
select tests.ok((select logo_path from public.public_firms) is null, 'logo is hidden once the subscription is cancelled');
select tests.ok((select premium from public.public_firms) = false, 'cancelled firms are no longer premium');
select tests.reset();
select tests.ok((select logo_path from public.firms where id = (select fa from ids)) is not null, 'the stored logo path is kept for re-subscription');

update public.firm_billing set status = 'past_due' where firm_id = (select fa from ids);
select tests.ok(public.firm_is_premium((select fa from ids)), 'past_due keeps premium during the card-retry grace period');
update public.firm_billing set status = 'trialing' where firm_id = (select fa from ids);
select tests.ok(public.firm_is_premium((select fa from ids)), 'trialing counts as premium');
update public.firm_billing set status = 'unpaid' where firm_id = (select fa from ids);
select tests.ok(not public.firm_is_premium((select fa from ids)), 'unpaid is not premium');
update public.firm_billing set status = 'canceled' where firm_id = (select fa from ids);

------------------------------------------------------------------ enquiries
set local role service_role;
insert into public.enquiries (firm_id, service, nation, property_price, quoted_total, name, email, message)
  values ((select fa from ids), 'purchase', 'Northern Ireland', 250000, 1500, 'Sam Client', 'sam@client.example', 'Leasehold flat');
reset role;

select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001');
select tests.ok(tests.count('select 1 from public.enquiries') = 1, 'a firm can read its own enquiries');
select tests.throws($$update public.enquiries set status = 'contacted'$$, '42501', 'a free firm cannot use enquiry tracking');
select tests.throws($$update public.enquiries set name = 'Tampered'$$, '42501', 'a firm cannot edit the client''s details');
select tests.reset();

select tests.as_user('bbbbbbbb-0000-0000-0000-000000000002');
select tests.ok(tests.count('select 1 from public.enquiries') = 0, 'a firm cannot read another firm''s enquiries');
select tests.reset();

update public.firm_billing set status = 'active' where firm_id = (select fa from ids);
select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001');
update public.enquiries set status = 'contacted', note = 'Called, quote sent';
select tests.ok((select status from public.enquiries) = 'contacted', 'a premium firm can track enquiry status and notes');
select tests.throws($$update public.enquiries set status = 'won'$$, '23514', 'enquiry status must be one of the allowed values');
select tests.reset();

------------------------------------------------------------------ logo storage
select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001');
insert into storage.objects (bucket_id, name) values ('logos', (select fa from ids)::text || '/logo.png');
select tests.ok(true, 'a premium firm can upload into its own logo folder');
select tests.throws(format($$insert into storage.objects (bucket_id, name) values ('logos', '%s/logo.png')$$, (select fb from ids)), '42501', 'a firm cannot upload into another firm''s folder');
select tests.throws($$insert into storage.objects (bucket_id, name) values ('other', 'x/y.png')$$, null, 'uploads outside the logos bucket are refused');
select tests.reset();

update public.firm_billing set status = 'canceled' where firm_id = (select fa from ids);
select tests.as_user('aaaaaaaa-0000-0000-0000-000000000001');
select tests.throws(format($$insert into storage.objects (bucket_id, name) values ('logos', '%s/second.png')$$, (select fa from ids)), '42501', 'a lapsed firm cannot upload new logos');
delete from storage.objects where bucket_id = 'logos';
select tests.ok(tests.count('select 1 from storage.objects') = 0, 'a lapsed firm can still delete its own logo');
select tests.reset();

------------------------------------------------------------------ cascade
delete from auth.users where id = 'aaaaaaaa-0000-0000-0000-000000000001';
select tests.ok((select count(*) from public.firms where name = 'Strangford Solicitors') = 0, 'deleting a user removes the firm');
select tests.ok((select count(*) from public.firm_billing) = 0, 'deleting a user removes billing');
select tests.ok((select count(*) from public.enquiries) = 0, 'deleting a user removes enquiries');

rollback;
\echo ALL SQL TESTS PASSED
