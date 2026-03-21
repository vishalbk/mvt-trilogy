-- Weekly aggregation view for trend analysis (S6-06)
-- Aggregates signals into weekly buckets with rolling averages
-- Used by analytics report Cloud Function

SELECT
  DATE_TRUNC(DATE(date), WEEK(MONDAY)) as week_start,
  'inequality' as category,
  source,
  AVG(value) as avg_value,
  MAX(value) as max_value,
  MIN(value) as min_value,
  STDDEV(value) as std_value,
  COUNT(*) as signal_count,
  AVG(value) - LAG(AVG(value)) OVER (
    PARTITION BY source ORDER BY DATE_TRUNC(DATE(date), WEEK(MONDAY))
  ) as week_over_week_change
FROM `mvt_analytics.inequality_signals`
WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
GROUP BY week_start, source

UNION ALL

SELECT
  DATE_TRUNC(DATE(date), WEEK(MONDAY)) as week_start,
  'sentiment' as category,
  event_type as source,
  AVG(severity) as avg_value,
  MAX(severity) as max_value,
  MIN(severity) as min_value,
  STDDEV(severity) as std_value,
  COUNT(*) as signal_count,
  AVG(severity) - LAG(AVG(severity)) OVER (
    PARTITION BY event_type ORDER BY DATE_TRUNC(DATE(date), WEEK(MONDAY))
  ) as week_over_week_change
FROM `mvt_analytics.sentiment_events`
WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
GROUP BY week_start, source

UNION ALL

SELECT
  DATE_TRUNC(DATE(date), WEEK(MONDAY)) as week_start,
  'sovereign' as category,
  country as source,
  AVG(risk_score) as avg_value,
  MAX(risk_score) as max_value,
  MIN(risk_score) as min_value,
  STDDEV(risk_score) as std_value,
  COUNT(*) as signal_count,
  AVG(risk_score) - LAG(AVG(risk_score)) OVER (
    PARTITION BY country ORDER BY DATE_TRUNC(DATE(date), WEEK(MONDAY))
  ) as week_over_week_change
FROM `mvt_analytics.sovereign_indicators`
WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
GROUP BY week_start, source

ORDER BY week_start DESC, category, source
