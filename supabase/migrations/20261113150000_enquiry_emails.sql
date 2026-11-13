-- Email firms about new enquiries: after each insert, call the notify-enquiry edge function.
alter table public.enquiries add column if not exists emailed_at timestamptz;

create extension if not exists pg_net with schema extensions;

create or replace function public.notify_enquiry_email() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
begin
  perform net.http_post(
    url := 'https://dtsxrmmdnyjmplhoyinv.supabase.co/functions/v1/notify-enquiry',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := jsonb_build_object('id', new.id)
  );
  return new;
end $$;
revoke all on function public.notify_enquiry_email() from public, anon, authenticated;

drop trigger if exists enquiries_notify_email on public.enquiries;
create trigger enquiries_notify_email after insert on public.enquiries
  for each row execute function public.notify_enquiry_email();
