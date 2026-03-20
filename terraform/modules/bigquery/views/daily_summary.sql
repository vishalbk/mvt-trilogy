-- Daily summary view for dashboard consumption
-- Aggregates inequality signals, sentiment events, and sovereign indicators
-- into a unified daily summary for KPI monitoring

SELECT
  DATE(date) as report_date,
  'inequality' as category,
  AVG(value) as avg_value,
  MAX(value) as max_value,
  MIN(value) as min_value,
  COUNT(*) as signal_count
FROM `mvt_analytics.inequality_signals`
WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY report_date
UNION ALL
SELECT
  DATE(date) as report_date,
  'sentiment' as category,
  AVG(severity) as avg_value,
  MAX(severity) as max_value,
  MIN(severity) as min_value,
  COUNT(*) as signal_count
FROM `mvt_analytics.sentiment_events`
WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY report_date
UNION ALL
SELECT
  DATE(date) as report_date,
  'sovereign' as category,
  AVG(risk_score) as avg_value,
  MAX(risk_score) as max_value,
  MIN(risk_score) as min_value,
  COUNT(*) as signal_count
FROM `mvt_analytics.sovereign_indicators`
WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY report_date
ORDER BY report_date DESC, category
