import * as cdk from "aws-cdk-lib";
import * as events from "aws-cdk-lib/aws-events";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";

export interface EventsStackProps extends cdk.StackProps {
  signalsTable: dynamodb.Table;
  dashboardStateTable: dynamodb.Table;
}

export class EventsStack extends cdk.Stack {
  public readonly eventBus: events.EventBus;

  // Ingestion schedule rules (referenced by IngestionStack)
  public readonly fredSchedule: events.Rule;
  public readonly trendsSchedule: events.Rule;
  public readonly finnhubSchedule: events.Rule;
  public readonly gdeltSchedule: events.Rule;
  public readonly worldbankSchedule: events.Rule;
  public readonly yfinanceSchedule: events.Rule;

  constructor(scope: Construct, id: string, props: EventsStackProps) {
    super(scope, id, props);

    // Central event bus for all cross-dashboard signals
    this.eventBus = new events.EventBus(this, "MVTEventBus", {
      eventBusName: "mvt-signals-bus",
    });

    // Archive events for 7 days (useful for replay/debugging)
    new events.Archive(this, "EventArchive", {
      sourceEventBus: this.eventBus,
      archiveName: "mvt-event-archive",
      retention: cdk.Duration.days(7),
      eventPattern: { source: [{ prefix: "mvt." }] as any },
    });

    // === INGESTION SCHEDULES ===

    // FRED API: hourly (24 calls/day — well within generous limits)
    this.fredSchedule = new events.Rule(this, "FredSchedule", {
      ruleName: "mvt-fred-hourly",
      schedule: events.Schedule.rate(cdk.Duration.hours(1)),
    });

    // Google Trends (pytrends): every 4 hours (6 calls/day — safe for rate limits)
    this.trendsSchedule = new events.Rule(this, "TrendsSchedule", {
      ruleName: "mvt-trends-4h",
      schedule: events.Schedule.rate(cdk.Duration.hours(4)),
    });

    // Finnhub: every 2 minutes (720 calls/day — 60/min limit is safe)
    this.finnhubSchedule = new events.Rule(this, "FinnhubSchedule", {
      ruleName: "mvt-finnhub-2min",
      schedule: events.Schedule.rate(cdk.Duration.minutes(2)),
    });

    // GDELT via BigQuery: every 15 minutes (96 calls/day)
    this.gdeltSchedule = new events.Rule(this, "GdeltSchedule", {
      ruleName: "mvt-gdelt-15min",
      schedule: events.Schedule.rate(cdk.Duration.minutes(15)),
    });

    // World Bank: daily (indicators update quarterly, so daily is plenty)
    this.worldbankSchedule = new events.Rule(this, "WorldBankSchedule", {
      ruleName: "mvt-worldbank-daily",
      schedule: events.Schedule.rate(cdk.Duration.days(1)),
    });

    // yfinance (VIX, FX, ETFs): every 5 minutes (288 calls/day)
    this.yfinanceSchedule = new events.Rule(this, "YfinanceSchedule", {
      ruleName: "mvt-yfinance-5min",
      schedule: events.Schedule.rate(cdk.Duration.minutes(5)),
    });

    // Outputs
    new cdk.CfnOutput(this, "EventBusArn", { value: this.eventBus.eventBusArn });
    new cdk.CfnOutput(this, "EventBusName", { value: this.eventBus.eventBusName });
  }
}
