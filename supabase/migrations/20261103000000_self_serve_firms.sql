-- Compare Legal: self-serve firm accounts, subscriptions and enquiries.
--
-- Model
--   * Every auth user is a law firm. A firm row is created automatically at sign-up.
--   * Firms edit only their own row (column-level grants below). Clients read firms through
--     the public_firms view, which only exposes listed, non-suspended firms.
--   * Billing rows are written only by the Stripe webhook (service role). Clients can read
--     their own row but never change it.
--   * Premium = firm_billing.status in active / trialing / past_due (past_due is a grace
--     period while Stripe retries the card).

------------------------------------------------------------------------------------------
-- firms
------------------------------------------------------------------------------------------
create table public.firms (
  id          uuid primary key default gen_random_uuid(),
  owner_id    uuid not null unique references auth.users (id) on delete cascade,
  name        text not null check (char_length(name) between 1 and 120),
  nation      text not null check (nation in ('England', 'Wales', 'Scotland', 'Northern Ireland')),
  town        text not null default '' check (char_length(town) <= 80),
  address     text not null default '' check (char_length(address) <= 200),
  phone       text not null default '' check (char_length(phone) <= 40),
  email       text not null default '' check (char_length(email) <= 200),
  website     text not null default '' check (char_length(website) <= 200),
  reg_no      text not null default '' check (char_length(reg_no) <= 60),
  about       text not null default '' check (char_length(about) <= 300),
  remote      boolean not null default false,
  vat         boolean not null default true,
  color       text not null default '#24507A' check (color ~ '^#[0-9A-Fa-f]{6}$'),
  logo_path   text check (logo_path ~ '^[0-9a-f-]{36}/[A-Za-z0-9._-]{1,80}$'),
  services    jsonb not null default '{}'::jsonb
              check (jsonb_typeof(services) = 'object' and pg_column_size(services) < 100000),
  listed      boolean not null default false,
  suspended   boolean not null default false,   -- admin kill switch, service role only
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index firms_public_idx on public.firms (nation) where listed and not suspended;

alter table public.firms enable row level security;

create policy "firms: owner can read own row"
  on public.firms for select to authenticated
  using (owner_id = (select auth.uid()));

create policy "firms: owner can update own row"
  on public.firms for update to authenticated
  using (owner_id = (select auth.uid()))
  with check (owner_id = (select auth.uid()));

-- Rows are created by the sign-up trigger and removed by cascade; clients never insert/delete.
revoke all on public.firms from anon, authenticated;
grant select on public.firms to authenticated;
grant update (name, nation, town, address, phone, email, website, reg_no, about,
              remote, vat, color, logo_path, services, listed)
  on public.firms to authenticated;

------------------------------------------------------------------------------------------
-- billing (written only by the Stripe webhook / checkout functions using the service role)
------------------------------------------------------------------------------------------
create table public.firm_billing (
  firm_id                 uuid primary key references public.firms (id) on delete cascade,
  stripe_customer_id      text unique,
  stripe_subscription_id  text unique,
  status                  text not null default 'none',
  price_id                text,
  current_period_end      timestamptz,
  cancel_at_period_end    boolean not null default false,
  updated_at              timestamptz not null default now()
);

alter table public.firm_billing enable row level security;

create policy "billing: owner can read own row"
  on public.firm_billing for select to authenticated
  using (exists (select 1 from public.firms f
                 where f.id = firm_id and f.owner_id = (select auth.uid())));

revoke all on public.firm_billing from anon, authenticated;
grant select (firm_id, status, current_period_end, cancel_at_period_end, updated_at)
  on public.firm_billing to authenticated;

create function public.firm_is_premium(p_firm uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.firm_billing b
    where b.firm_id = p_firm and b.status in ('active', 'trialing', 'past_due')
  );
$$;

-- Postgres checks function privileges against the caller even inside a view, so the public
-- listing needs anon to be able to call this. It only returns a boolean (already public as the
-- `premium` column of public_firms) and runs with definer rights, so nothing else is exposed.
revoke execute on function public.firm_is_premium(uuid) from public;
grant execute on function public.firm_is_premium(uuid) to anon, authenticated, service_role;

------------------------------------------------------------------------------------------
-- enquiries (quote requests from clients)
------------------------------------------------------------------------------------------
create table public.enquiries (
  id              uuid primary key default gen_random_uuid(),
  firm_id         uuid not null references public.firms (id) on delete cascade,
  service         text not null check (char_length(service) <= 40),
  nation          text check (char_length(nation) <= 40),
  property_price  numeric check (property_price >= 0),
  quoted_total    numeric check (quoted_total >= 0),
  name            text not null check (char_length(name) between 1 and 120),
  email           text not null check (char_length(email) between 3 and 200),
  phone           text not null default '' check (char_length(phone) <= 40),
  message         text not null default '' check (char_length(message) <= 2000),
  status          text not null default 'new' check (status in ('new', 'contacted', 'instructed', 'closed')),
  note            text not null default '' check (char_length(note) <= 2000),
  created_at      timestamptz not null default now(),
  emailed_at      timestamptz
);

create index enquiries_firm_created_idx on public.enquiries (firm_id, created_at desc);

alter table public.enquiries enable row level security;

create policy "enquiries: owner can read"
  on public.enquiries for select to authenticated
  using (exists (select 1 from public.firms f
                 where f.id = firm_id and f.owner_id = (select auth.uid())));

create policy "enquiries: owner can update"
  on public.enquiries for update to authenticated
  using (exists (select 1 from public.firms f
                 where f.id = firm_id and f.owner_id = (select auth.uid())))
  with check (exists (select 1 from public.firms f
                      where f.id = firm_id and f.owner_id = (select auth.uid())));

-- Inserts come from the submit-enquiry Edge Function (service role), which validates and rate-limits.
revoke all on public.enquiries from anon, authenticated;
grant select on public.enquiries to authenticated;
grant update (status, note) on public.enquiries to authenticated;

------------------------------------------------------------------------------------------
-- triggers
------------------------------------------------------------------------------------------
-- Create a firm for every new auth user, using the name and nation given at registration.
create function public.handle_new_user()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_name   text := left(trim(coalesce(new.raw_user_meta_data ->> 'firm_name', '')), 120);
  v_nation text := coalesce(new.raw_user_meta_data ->> 'nation', 'England');
begin
  if v_name = '' then
    v_name := coalesce(nullif(split_part(coalesce(new.email, ''), '@', 1), ''), 'New firm');
  end if;
  if v_nation not in ('England', 'Wales', 'Scotland', 'Northern Ireland') then
    v_nation := 'England';
  end if;
  insert into public.firms (owner_id, name, nation, email)
  values (new.id, v_name, v_nation, left(coalesce(new.email, ''), 200));
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Keep updated_at honest, and make the logo a Premium feature (clearing a logo is always allowed).
create function public.firms_before_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  if new.logo_path is not null
     and new.logo_path is distinct from old.logo_path
     and not public.firm_is_premium(new.id) then
    raise exception 'Logo upload is a Premium feature' using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger firms_before_update
  before update on public.firms
  for each row execute function public.firms_before_update();

-- Enquiry tracking (status and notes) is a Premium feature. Every firm still receives its enquiries.
create function public.enquiries_before_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (new.status is distinct from old.status or new.note is distinct from old.note)
     and not public.firm_is_premium(old.firm_id) then
    raise exception 'Enquiry tracking is a Premium feature' using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger enquiries_before_update
  before update on public.enquiries
  for each row execute function public.enquiries_before_update();

------------------------------------------------------------------------------------------
-- public listing view
------------------------------------------------------------------------------------------
-- Runs with the view owner's rights on purpose: it is the only way clients read firms, and it
-- exposes just the columns below, only for listed and non-suspended firms. The logo is only
-- returned while the firm is Premium. (Supabase's linter flags security-definer views; this one
-- is intentional.)
create view public.public_firms with (security_invoker = false) as
select
  f.id, f.name, f.nation, f.town, f.address, f.phone, f.email, f.website,
  f.reg_no, f.about, f.remote, f.vat, f.color, f.services, f.updated_at,
  public.firm_is_premium(f.id) as premium,
  case when public.firm_is_premium(f.id) then f.logo_path end as logo_path
from public.firms f
where f.listed and not f.suspended;

-- Supabase grants ALL on new public views to anon/authenticated by default, and this view is
-- auto-updatable and runs with the owner's rights, so strip everything except SELECT.
revoke all on public.public_firms from public, anon, authenticated;
grant select on public.public_firms to anon, authenticated;

------------------------------------------------------------------------------------------
-- logo storage (public bucket; only Premium firms can write, only into their own folder)
------------------------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('logos', 'logos', true, 524288, array['image/png', 'image/jpeg', 'image/webp'])
on conflict (id) do update
  set public = excluded.public,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

create function public.owns_firm_folder(p_folder text)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (select 1 from public.firms f
                 where f.id::text = p_folder and f.owner_id = (select auth.uid()));
$$;

create function public.owns_premium_firm_folder(p_folder text)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (select 1 from public.firms f
                 where f.id::text = p_folder and f.owner_id = (select auth.uid())
                   and public.firm_is_premium(f.id));
$$;

revoke execute on function public.owns_firm_folder(text), public.owns_premium_firm_folder(text)
  from public, anon;
grant execute on function public.owns_firm_folder(text), public.owns_premium_firm_folder(text)
  to authenticated;

create policy "logos: premium owner can upload"
  on storage.objects for insert to authenticated
  with check (bucket_id = 'logos' and public.owns_premium_firm_folder((storage.foldername(name))[1]));

create policy "logos: premium owner can replace"
  on storage.objects for update to authenticated
  using (bucket_id = 'logos' and public.owns_premium_firm_folder((storage.foldername(name))[1]))
  with check (bucket_id = 'logos' and public.owns_premium_firm_folder((storage.foldername(name))[1]));

create policy "logos: owner can delete"
  on storage.objects for delete to authenticated
  using (bucket_id = 'logos' and public.owns_firm_folder((storage.foldername(name))[1]));
