SELECT
  PARSE_DATE('%Y%m%d', CAST(SQLDATE AS STRING)) as event_date,
  EventCode,
  EventRootCode,
  Actor1CountryCode as actor1_country,
  Actor2CountryCode as actor2_country,
  GoldsteinScale as goldstein_scale,
  NumMentions as num_mentions,
  NumSources as num_sources,
  NumArticles as num_articles,
  AvgTone as avg_tone,
  ActionGeo_CountryCode as action_geo_country,
  ActionGeo_Lat as action_geo_lat,
  ActionGeo_Long as action_geo_long,
  CURRENT_TIMESTAMP() as ingested_at
FROM `gdelt-bq.gdeltv2.events`
WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
  AND EventRootCode IN ('14','17','18','19','20')
  AND NumMentions > 5
ORDER BY NumMentions DESC
LIMIT 1000
