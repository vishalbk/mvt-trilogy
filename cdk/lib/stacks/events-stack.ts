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

    // Note: Ingestion schedule rules are defined in IngestionStack
    // to avoid cross-stack dependency cycles with Lambda targets.

    // Outputs
    new cdk.CfnOutput(this, "EventBusArn", { value: this.eventBus.eventBusArn });
    new cdk.CfnOutput(this, "EventBusName", { value: this.eventBus.eventBusName });
  }
}
