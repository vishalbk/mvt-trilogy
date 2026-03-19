import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import * as path from "path";

export interface ProcessingStackProps extends cdk.StackProps {
  signalsTable: dynamodb.Table;
  dashboardStateTable: dynamodb.Table;
  auditTable: dynamodb.Table;
  eventBus: events.EventBus;
}

export class ProcessingStack extends cdk.Stack {
  public readonly functions: lambda.Function[] = [];

  constructor(scope: Construct, id: string, props: ProcessingStackProps) {
    super(scope, id, props);

    const { signalsTable, dashboardStateTable, auditTable, eventBus } = props;

    // === LAMBDA PROCESSING FUNCTIONS ===

    // 1. Inequality Scorer
    // Triggered by EventBridge rule matching source=mvt.ingestion.inequality
    const inequalityFunction = this.createProcessingFunction(
      "InequalityScorer",
      "inequality-scorer",
      "inequality_scorer.handler",
      { SIGNALS_TABLE: signalsTable.tableName, EVENT_BUS_NAME: eventBus.eventBusName },
      signalsTable,
      dashboardStateTable,
      auditTable,
      eventBus
    );
    this.attachEventBridgeRule(
      eventBus,
      "InequalityProcessingRule",
      inequalityFunction,
      { source: ["mvt.ingestion.inequality"] }
    );
    this.functions.push(inequalityFunction);

    // 2. Sentiment Aggregator
    // Triggered by EventBridge rule matching source=mvt.ingestion.sentiment
    const sentimentFunction = this.createProcessingFunction(
      "SentimentAggregator",
      "sentiment-aggregator",
      "sentiment_aggregator.handler",
      { SIGNALS_TABLE: signalsTable.tableName, EVENT_BUS_NAME: eventBus.eventBusName },
      signalsTable,
      dashboardStateTable,
      auditTable,
      eventBus
    );
    this.attachEventBridgeRule(
      eventBus,
      "SentimentProcessingRule",
      sentimentFunction,
      { source: ["mvt.ingestion.sentiment"] }
    );
    this.functions.push(sentimentFunction);

    // 3. Contagion Modeler
    // Triggered by EventBridge rule matching source=mvt.ingestion.sovereign
    const contagionFunction = this.createProcessingFunction(
      "ContagionModeler",
      "contagion-modeler",
      "contagion_modeler.handler",
      { SIGNALS_TABLE: signalsTable.tableName, EVENT_BUS_NAME: eventBus.eventBusName },
      signalsTable,
      dashboardStateTable,
      auditTable,
      eventBus,
      512
    );
    this.attachEventBridgeRule(
      eventBus,
      "ContagionProcessingRule",
      contagionFunction,
      { source: ["mvt.ingestion.sovereign"] }
    );
    this.functions.push(contagionFunction);

    // 4. Cross-Dashboard Router
    // Triggered by EventBridge rule matching source=mvt.processing.*
    const routerFunction = this.createProcessingFunction(
      "CrossDashboardRouter",
      "cross-dashboard-router",
      "cross_dashboard_router.handler",
      { SIGNALS_TABLE: signalsTable.tableName, EVENT_BUS_NAME: eventBus.eventBusName },
      signalsTable,
      dashboardStateTable,
      auditTable,
      eventBus
    );
    this.attachEventBridgeRule(
      eventBus,
      "RouterProcessingRule",
      routerFunction,
      { source: [{ prefix: "mvt.processing." }] }
    );
    this.functions.push(routerFunction);

    // 5. Vulnerability Composite
    // Triggered by EventBridge rule matching source=mvt.processing.score
    const compositeFunction = this.createProcessingFunction(
      "VulnerabilityComposite",
      "vulnerability-composite",
      "vulnerability_composite.handler",
      { SIGNALS_TABLE: signalsTable.tableName, EVENT_BUS_NAME: eventBus.eventBusName },
      signalsTable,
      dashboardStateTable,
      auditTable,
      eventBus
    );
    this.attachEventBridgeRule(
      eventBus,
      "CompositeProcessingRule",
      compositeFunction,
      { source: ["mvt.processing.score"] }
    );
    this.functions.push(compositeFunction);

    // Outputs
    new cdk.CfnOutput(this, "ProcessingFunctionsCount", {
      value: String(this.functions.length),
    });
  }

  /**
   * Helper to create a processing Lambda function with DynamoDB and EventBridge permissions
   */
  private createProcessingFunction(
    constructId: string,
    functionName: string,
    handler: string,
    environment: Record<string, string>,
    signalsTable: dynamodb.Table,
    dashboardStateTable: dynamodb.Table,
    auditTable: dynamodb.Table,
    eventBus: events.EventBus,
    memory: number = 256
  ): lambda.Function {
    const lambdaFunction = new lambda.Function(this, constructId, {
      functionName: `mvt-${functionName}`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler,
      code: lambda.Code.fromAsset(
        path.join(__dirname, `../handlers/processing/${functionName}`)
      ),
      memorySize: memory,
      timeout: this.getTimeoutForFunction(functionName),
      environment,
    });

    // Grant read/write access to signals table
    signalsTable.grantReadWriteData(lambdaFunction);

    // Grant read/write access to dashboard state table
    dashboardStateTable.grantReadWriteData(lambdaFunction);

    // Grant write access to audit table
    auditTable.grantWriteData(lambdaFunction);

    // Grant EventBridge put-events access
    eventBus.grantPutEventsTo(lambdaFunction);

    return lambdaFunction;
  }

  /**
   * Get timeout based on function type
   */
  private getTimeoutForFunction(functionName: string): cdk.Duration {
    switch (functionName) {
      case "contagion-modeler":
        return cdk.Duration.seconds(30);
      default:
        return cdk.Duration.seconds(15);
    }
  }

  /**
   * Attach EventBridge rule to trigger Lambda function
   */
  private attachEventBridgeRule(
    eventBus: events.EventBus,
    ruleName: string,
    lambdaFunction: lambda.Function,
    eventPattern: any
  ): void {
    const rule = new events.Rule(this, ruleName, {
      ruleName: `mvt-${ruleName.toLowerCase()}`,
      eventBus,
      eventPattern,
    });

    rule.addTarget(
      new targets.LambdaFunction(lambdaFunction, {
        maxEventAge: cdk.Duration.hours(1),
        retryAttempts: 2,
      })
    );
  }
}
