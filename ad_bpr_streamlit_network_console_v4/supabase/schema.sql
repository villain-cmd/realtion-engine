-- Run once in Supabase SQL Editor.
-- The service_role key is used only by Streamlit Secrets / GitHub Actions Secrets.

create table if not exists public.source_records (
  source text not null,
  dataset text not null,
  record_key text not null,
  record_hash text not null,
  occurred_at timestamptz,
  ingested_at timestamptz not null default now(),
  payload jsonb not null,
  primary key (source, dataset, record_key)
);

create index if not exists source_records_dataset_occurred_idx
  on public.source_records (dataset, occurred_at desc);

create index if not exists source_records_payload_gin_idx
  on public.source_records using gin (payload);

alter table public.source_records enable row level security;

-- No anon/authenticated policy is intentional. The browser must never receive
-- the service_role key; only the server-side Streamlit app and scheduled job use it.

create or replace view public.latest_source_sync
with (security_invoker = true) as
select
  source,
  dataset,
  count(*) as row_count,
  max(ingested_at) as last_ingested_at,
  max(occurred_at) as latest_occurred_at
from public.source_records
group by source, dataset;
