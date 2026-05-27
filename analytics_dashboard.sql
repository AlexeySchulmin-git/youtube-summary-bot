-- YouTube Summary Bot - Analytics Dashboard SQL
-- Run this whole file in Supabase SQL Editor (single window).
-- It creates dashboard views you can plug into Metabase/Supabase charts.

-- 1) Core daily activity
create or replace view public.v_analytics_daily as
select
  date_trunc('day', e.event_at)::date as day,
  count(distinct e.telegram_user_id) as dau,
  count(*) as events_count,
  count(distinct e.telegram_user_id) filter (where e.event_name = 'summary_success') as users_with_summary,
  count(*) filter (where e.event_name = 'summary_success') as summary_success_count,
  count(*) filter (where e.event_name = 'summary_failed') as summary_failed_count
from public.analytics_events e
group by 1
order by 1;

-- 2) Rolling KPI (WAU/MAU + conversion)
create or replace view public.v_analytics_kpi as
with base as (
  select
    count(distinct telegram_user_id) filter (where event_at >= now() - interval '1 day') as dau_now,
    count(distinct telegram_user_id) filter (where event_at >= now() - interval '7 day') as wau_now,
    count(distinct telegram_user_id) filter (where event_at >= now() - interval '30 day') as mau_now
  from public.analytics_events
),
funnel as (
  select
    telegram_user_id,
    max(case when event_name = 'command_used' and meta->>'command' = 'start' then 1 else 0 end) as has_start,
    max(case when event_name = 'command_used' and meta->>'command' = 'search' then 1 else 0 end) as has_search,
    max(case when event_name = 'summary_success' then 1 else 0 end) as has_summary
  from public.analytics_events
  group by telegram_user_id
)
select
  b.dau_now,
  b.wau_now,
  b.mau_now,
  count(*) filter (where f.has_start = 1) as funnel_start_users,
  count(*) filter (where f.has_start = 1 and f.has_search = 1) as funnel_search_users,
  count(*) filter (where f.has_start = 1 and f.has_search = 1 and f.has_summary = 1) as funnel_summary_users,
  round(
    100.0 * count(*) filter (where f.has_start = 1 and f.has_search = 1 and f.has_summary = 1)
    / nullif(count(*) filter (where f.has_start = 1), 0),
    2
  ) as start_to_summary_conversion_pct
from base b
cross join funnel f
group by b.dau_now, b.wau_now, b.mau_now;

-- 3) Commands usage
create or replace view public.v_analytics_commands as
select
  coalesce(meta->>'command', '(none)') as command,
  count(*) as total,
  count(distinct telegram_user_id) as users
from public.analytics_events
where event_name = 'command_used'
group by 1
order by 2 desc;

-- 4) Buttons usage
create or replace view public.v_analytics_buttons as
select
  coalesce(meta->>'button', '(none)') as button,
  count(*) as total,
  count(distinct telegram_user_id) as users
from public.analytics_events
where event_name = 'button_clicked'
group by 1
order by 2 desc;

-- 5) Retention D1 / D7 / D30 by cohort
create or replace view public.v_analytics_retention as
with first_seen as (
  select
    telegram_user_id,
    date(first_seen_at) as cohort_day
  from public.analytics_users
  where first_seen_at is not null
),
activity as (
  select distinct
    telegram_user_id,
    date(event_at) as active_day
  from public.analytics_events
),
joined as (
  select
    f.cohort_day,
    f.telegram_user_id,
    max(case when a.active_day = f.cohort_day + interval '1 day' then 1 else 0 end) as d1,
    max(case when a.active_day = f.cohort_day + interval '7 day' then 1 else 0 end) as d7,
    max(case when a.active_day = f.cohort_day + interval '30 day' then 1 else 0 end) as d30
  from first_seen f
  left join activity a on a.telegram_user_id = f.telegram_user_id
  group by f.cohort_day, f.telegram_user_id
)
select
  cohort_day,
  count(*) as new_users,
  sum(d1) as retained_d1,
  sum(d7) as retained_d7,
  sum(d30) as retained_d30,
  round(100.0 * sum(d1) / nullif(count(*), 0), 2) as retention_d1_pct,
  round(100.0 * sum(d7) / nullif(count(*), 0), 2) as retention_d7_pct,
  round(100.0 * sum(d30) / nullif(count(*), 0), 2) as retention_d30_pct
from joined
group by cohort_day
order by cohort_day desc;

-- 6) Traffic by language
create or replace view public.v_analytics_languages as
select
  coalesce(language_code, 'unknown') as language_code,
  count(*) as users
from public.analytics_users
group by 1
order by 2 desc;

-- 7) Quick one-screen query (returns one row with JSON blocks)
create or replace view public.v_analytics_dashboard_one_row as
select jsonb_build_object(
  'kpi', (select to_jsonb(k) from public.v_analytics_kpi k),
  'last_30_days', (
    select jsonb_agg(to_jsonb(d) order by d.day)
    from (
      select * from public.v_analytics_daily
      where day >= current_date - interval '30 day'
      order by day
    ) d
  ),
  'top_commands', (
    select jsonb_agg(to_jsonb(c))
    from (
      select * from public.v_analytics_commands limit 10
    ) c
  ),
  'top_buttons', (
    select jsonb_agg(to_jsonb(b))
    from (
      select * from public.v_analytics_buttons limit 10
    ) b
  ),
  'retention_last_30_cohorts', (
    select jsonb_agg(to_jsonb(r))
    from (
      select * from public.v_analytics_retention limit 30
    ) r
  )
) as dashboard;
