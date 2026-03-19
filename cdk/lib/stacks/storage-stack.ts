import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";

export class StorageStack extends cdk.Stack {
  public readonly signalsTable: dynamodb.Table;
  public readonly connectionsTable: dynamodb.Table;
  public readonly dashboardStateTable: dynamodb.Table;
  public readonly auditTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Table 1: Real-time signal data from all sources
    this.signalsTable = new dynamodb.Table(this, "SignalsTable", {
      tableName: "mvt-signals",
      partitionKey: { name: "dashboard", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "signalId_timestamp", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, // Free tier: 25 RCU/WCU
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: "ttl",
      stream: dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
    });

    this.signalsTable.addGlobalSecondaryIndex({
      indexName: "by-source",
      partitionKey: { name: "source", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "signalId_timestamp", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Table 2: WebSocket connection tracking
    this.connectionsTable = new dynamodb.Table(this, "ConnectionsTable", {
      tableName: "mvt-connections",
      partitionKey: { name: "connectionId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: "ttl",
    });

    // Table 3: Current computed dashboard state
    this.dashboardStateTable = new dynamodb.Table(this, "DashboardStateTable", {
      tableName: "mvt-dashboard-state",
      partitionKey: { name: "dashboard", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "panel", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      stream: dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
    });

    // Table 4: Event audit trail
    this.auditTable = new dynamodb.Table(this, "AuditTable", {
      tableName: "mvt-audit",
      partitionKey: { name: "eventId", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "timestamp", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: "ttl",
    });

    this.auditTable.addGlobalSecondaryIndex({
      indexName: "by-event-type",
      partitionKey: { name: "eventType", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "timestamp", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // Outputs
    new cdk.CfnOutput(this, "SignalsTableArn", { value: this.signalsTable.tableArn });
    new cdk.CfnOutput(this, "ConnectionsTableArn", { value: this.connectionsTable.tableArn });
    new cdk.CfnOutput(this, "DashboardStateTableArn", { value: this.dashboardStateTable.tableArn });
  }
}
