-- Supabase schema for YouTube Summary Bot (MVP)

create extension if not exists pgcrypto;

create table if not exists public.user_profiles (
  id uuid primary key default gen_random_uuid(),
  telegram_user_id bigint not null unique,
  username text,
  first_name text,
  last_name text,
  created_at timestamptz not null default now()
);

create table if not exists public.summaries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.user_profiles(id) on delete cascade,
  video_id text not null,
  video_url text not null,
  transcript_source text not null check (transcript_source in ('youtube-transcript-api', 'supadata')),
  summary_markdown text not null,
  chunk_count int not null default 1,
  model_analyst_small text,
  model_analyst_large text,
  model_synthesizer text,
  created_at timestamptz not null default now()
);

create index if not exists idx_summaries_user_created_at
  on public.summaries(user_id, created_at desc);

create index if not exists idx_summaries_video_id
  on public.summaries(video_id);

create table if not exists public.summary_feedback (
  id uuid primary key default gen_random_uuid(),
  summary_id uuid not null references public.summaries(id) on delete cascade,
  user_id uuid not null references public.user_profiles(id) on delete cascade,
  rating smallint check (rating between 1 and 5),
  liked boolean,
  comment text,
  created_at timestamptz not null default now(),
  unique(summary_id, user_id)
);

alter table public.user_profiles enable row level security;
alter table public.summaries enable row level security;
alter table public.summary_feedback enable row level security;

-- NOTE: add auth-based RLS policies later when web app auth is connected.
