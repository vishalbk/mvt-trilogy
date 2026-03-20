-- Daily correlation between inequality distress signals and sentiment triggers
-- Computes Pearson correlation over a 90-day rolling window
SELECT
  CORR(i.avg_distress, s.avg_trigger) as distress_trigger_correlation,
  COUNT(*) as data_points,
  CURRENT_TIMESTAMP() as computed_at
FROM (
  SELECT date, AVG(value) as avg_distress
  FROM `mvt_analytics.inequality_signals`
  WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
  GROUP BY date
) i
JOIN (
  SELECT date, AVG(severity) as avg_trigger
  FROM `mvt_analytics.sentiment_events`
  WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
  GROUP BY date
) s ON i.date = s.date;
