-- Monthly aggregation view for executive reporting (S6-06)
-- Provides monthly KPI summaries with trend indicators
-- Used by automated report generation

SELECT
  DATE_TRUNC(DATE(date), MONTH) as month_start,
  'inequality' as category,
  source,
  AVG(value) as avg_value,
  MAX(value) as max_value,
  MIN(value) as min_value,
  STDDEV(value) as std_value,
  COUNT(*) as signal_count,
  -- Month-over-month percentage change
  SAFE_DIVIDE(
    AVG(value) - LAG(AVG(value)) OVER (
      PARTITION BY source ORDER BY DATE_TRUNC(DATE(date), MONTH)
    ),
    ABS(LAG(AVG(value)) OVER (
      PARTITION BY source ORDER BY DATE_TRUNC(DATE(date), MONTH)
    ))
  ) * 100 as mom_change_pct,
  -- Coefficient of variation (volatility measure)
  SAFE_DIVIDE(STDDEV(value), AVG(value)) * 100 as cv_pct
FROM `mvt_analytics.inequality_signals`
WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
GROUP BY month_start, source

UNION ALL

SELECT
  DATE_TRUNC(DATE(date), MONTH) as month_start,
  'sentiment' as category,
  event_type as source,
  AVG(severity) as avg_value,
  MAX(severity) as max_value,
  MIN(severity) as min_value,
  STDDEV(severity) as std_value,
  COUNT(*) as signal_count,
  SAFE_DIVIDE(
    AVG(severity) - LAG(AVG(severity)) OVER (
      PARTITION BY event_type ORDER BY DATE_TRUNC(DATE(date), MONTH)
    ),
    ABS(LAG(AVG(severity)) OVER (
      PARTITION BY event_type ORDER BY DATE_TRUNC(DATE(date), MONTH)
    ))
  ) * 100 as mom_change_pct,
  SAFE_DIVIDE(STDDEV(severity), AVG(severity)) * 100 as cv_pct
FROM `mvt_analytics.sentiment_events`
WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
GROUP BY month_start, source

UNION ALL

SELECT
  DATE_TRUNC(DATE(date), MONTH) as month_start,
  'sovereign' as category,
  country as source,
  AVG(risk_score) as avg_value,
  MAX(risk_score) as max_value,
  MIN(risk_score) as min_value,
  STDDEV(risk_score) as std_value,
  COUNT(*) as signal_count,
  SAFE_DIVIDE(
    AVG(risk_score) - LAG(AVG(risk_score)) OVER (
      PARTITION BY country ORDER BY DATE_TRUNC(DATE(date), MONTH)
    ),
    ABS(LAG(AVG(risk_score)) OVER (
      PARTITION BY country ORDER BY DATE_TRUNC(DATE(date), MONTH)
    ))
  ) * 100 as mom_change_pct,
  SAFE_DIVIDE(STDDEV(risk_score), AVG(risk_score)) * 100 as cv_pct
FROM `mvt_analytics.sovereign_indicators`
WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
GROUP BY month_start, source

ORDER BY month_start DESC, category, source
