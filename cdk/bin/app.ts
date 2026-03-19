#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { StorageStack } from "../lib/stacks/storage-stack";
import { EventsStack } from "../lib/stacks/events-stack";
import { IngestionStack } from "../lib/stacks/ingestion-stack";
import { ProcessingStack } from "../lib/stacks/processing-stack";
import { RealtimeStack } from "../lib/stacks/realtime-stack";
import { FrontendStack } from "../lib/stacks/frontend-stack";
import { MonitoringStack } from "../lib/stacks/monitoring-stack";

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || "us-east-1",
};

const envName = app.node.tryGetContext("environment") || "dev";

// Stack dependency chain:
// Storage → Events → Ingestion → Processing → Realtime → Frontend → Monitoring

const storage = new StorageStack(app, `MVT-Storage-${envName}`, { env });

const events = new EventsStack(app, `MVT-Events-${envName}`, {
  env,
  signalsTable: storage.signalsTable,
  dashboardStateTable: storage.dashboardStateTable,
});

const ingestion = new IngestionStack(app, `MVT-Ingestion-${envName}`, {
  env,
  signalsTable: storage.signalsTable,
  eventBus: events.eventBus,
});

const processing = new ProcessingStack(app, `MVT-Processing-${envName}`, {
  env,
  signalsTable: storage.signalsTable,
  dashboardStateTable: storage.dashboardStateTable,
  auditTable: storage.auditTable,
  eventBus: events.eventBus,
});

const realtime = new RealtimeStack(app, `MVT-Realtime-${envName}`, {
  env,
  connectionsTable: storage.connectionsTable,
  dashboardStateTable: storage.dashboardStateTable,
  eventBus: events.eventBus,
});

const frontend = new FrontendStack(app, `MVT-Frontend-${envName}`, {
  env,
  websocketApi: realtime.websocketApi,
});

const monitoring = new MonitoringStack(app, `MVT-Monitoring-${envName}`, {
  env,
  ingestionFunctions: ingestion.functions,
  processingFunctions: processing.functions,
  realtimeFunctions: realtime.functions,
});

// Tag all stacks
for (const stack of [storage, events, ingestion, processing, realtime, frontend, monitoring]) {
  cdk.Tags.of(stack).add("Project", "MVT-Trilogy");
  cdk.Tags.of(stack).add("Environment", envName);
  cdk.Tags.of(stack).add("ManagedBy", "CDK");
  cdk.Tags.of(stack).add("BuiltBy", "AI-Agent-Swarm");
}
