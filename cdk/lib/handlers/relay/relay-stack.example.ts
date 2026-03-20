/**
 * Example CDK Stack for Event Relay
 *
 * This file demonstrates how to integrate the mvt-event-relay Lambda
 * into the CDK infrastructure (future enhancement).
 *
 * Currently, the relay is deployed independently via deploy.sh script.
 * To integrate into the CDK stack:
 * 1. Create a new file: cdk/lib/stacks/relay-stack.ts
 * 2. Copy the code from this file
 * 3. Update cdk/lib/app.ts to include this stack
 * 4. Run: cdk deploy RelayStack
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";
import * as path from "path";

export interface RelayStackProps extends cdk.StackProps {
  dashboardStateTable: dynamodb.Table;
  // Optional: reference to Secrets Manager secret containing GCP service account key
  gcpServiceAccountKeySecret?: secretsmanager.ISecret;
}

/**
 * Relay Stack
 *
 * Provides cross-cloud event relay from AWS to GCP.
 * Attaches event-relay Lambda to DynamoDB Stream for real-time signal forwarding.
 */
export class RelayStack extends cdk.Stack {
  public readonly eventRelayFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: RelayStackProps) {
    super(scope, id, props);

    const { dashboardStateTable, gcpServiceAccountKeySecret } = props;

    // === EVENT RELAY LAMBDA ===

    const eventRelayFunction = new lambda.Function(this, "EventRelayHandler", {
      functionName: "mvt-event-relay",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../handlers/relay/event-relay")
      ),
      memorySize: 256,
      timeout: cdk.Duration.seconds(30),
      description:
        "Relay DynamoDB signals to GCP Pub/Sub for cross-cloud event distribution",
      environment: {
        GCP_PROJECT_ID: "mvt-observer",
        GCP_PUBSUB_TOPIC: "mvt-signals",
        // Note: GCP_SA_KEY_JSON must be set separately via environment variable or Secrets Manager
        // See DEPLOYMENT_GUIDE.md for configuration instructions
      },
    });

    this.eventRelayFunction = eventRelayFunction;

    // === PERMISSIONS ===

    // Grant Lambda permission to read from DynamoDB Stream
    dashboardStateTable.grantStreamRead(eventRelayFunction);

    // If using Secrets Manager for GCP service account key
    if (gcpServiceAccountKeySecret) {
      gcpServiceAccountKeySecret.grantRead(eventRelayFunction);

      // Update environment variable to reference secret
      eventRelayFunction.addEnvironment(
        "GCP_SA_KEY_SECRET_ARN",
        gcpServiceAccountKeySecret.secretArn
      );
    }

    // === EVENT SOURCE MAPPING ===

    // Attach DynamoDB Stream as event source
    // Note: This will share the stream with the ws-broadcast Lambda in RealtimeStack
    const eventSource = eventRelayFunction.addEventSource(
      new cdk.aws_lambda_event_sources.DynamoEventSource(dashboardStateTable, {
        startingPosition: lambda.StartingPosition.LATEST,
        batchSize: 10,
        parallelizationFactor: 2,
        // Optional: add a maximum age to handle any backlog
        maxRecordAge: cdk.Duration.seconds(60),
        // Optional: report batch item failures for partial failure handling
        reportBatchItemFailures: true,
      })
    );

    // === MONITORING & ALARMS ===

    // Create CloudWatch alarms for error tracking
    const errorAlarm = new cdk.aws_cloudwatch.Alarm(this, "EventRelayErrors", {
      metric: new cdk.aws_cloudwatch.Metric({
        namespace: "AWS/Lambda",
        metricName: "Errors",
        dimensions: {
          FunctionName: eventRelayFunction.functionName,
        },
        statistic: "Sum",
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5,
      evaluationPeriods: 1,
      alarmDescription: "Alert when event relay Lambda errors exceed threshold",
    });

    // Create CloudWatch alarms for duration
    const durationAlarm = new cdk.aws_cloudwatch.Alarm(
      this,
      "EventRelayDuration",
      {
        metric: new cdk.aws_cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: "Duration",
          dimensions: {
            FunctionName: eventRelayFunction.functionName,
          },
          statistic: "Average",
          period: cdk.Duration.minutes(5),
        }),
        threshold: 20000, // 20 seconds in milliseconds
        evaluationPeriods: 1,
        alarmDescription: "Alert when event relay Lambda duration is high",
      }
    );

    // === LOG GROUP ===

    const logGroup = new cdk.aws_logs.LogGroup(this, "EventRelayLogs", {
      logGroupName: "/aws/lambda/mvt-event-relay",
      retention: cdk.aws_logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE,
    });

    // === OUTPUTS ===

    new cdk.CfnOutput(this, "EventRelayFunctionName", {
      value: eventRelayFunction.functionName,
      description: "Event relay Lambda function name",
    });

    new cdk.CfnOutput(this, "EventRelayFunctionArn", {
      value: eventRelayFunction.functionArn,
      description: "Event relay Lambda function ARN",
    });

    new cdk.CfnOutput(this, "EventRelayLogGroup", {
      value: logGroup.logGroupName,
      description: "CloudWatch log group for event relay",
    });

    new cdk.CfnOutput(this, "EventSourceMappingId", {
      value: eventSource.eventSourceMappingId!,
      description: "DynamoDB Stream to Lambda event source mapping ID",
    });
  }
}

/**
 * Usage in app.ts:
 *
 * import { RelayStack } from "./stacks/relay-stack";
 *
 * const relayStack = new RelayStack(app, "RelayStack", {
 *   dashboardStateTable: storageStack.dashboardStateTable,
 *   gcpServiceAccountKeySecret: gcpSecret, // Optional
 * });
 *
 * Then deploy with:
 *   cdk deploy RelayStack
 */

/**
 * Alternative Approach: Minimal Configuration
 *
 * If you prefer minimal CDK involvement, use the deploy.sh script:
 *
 *   cd cdk/lib/handlers/relay/event-relay
 *   ./deploy.sh
 *
 * Then manually set GCP credentials:
 *
 *   aws lambda update-function-configuration \
 *     --function-name mvt-event-relay \
 *     --environment Variables='{
 *       "GCP_PROJECT_ID":"mvt-observer",
 *       "GCP_PUBSUB_TOPIC":"mvt-signals",
 *       "GCP_SA_KEY_JSON":"<service-account-json>"
 *     }'
 *
 * This approach is currently recommended because:
 * 1. Sensitive GCP credentials should not be in CDK code
 * 2. Deployment script handles all AWS configuration automatically
 * 3. Easier to update Lambda code without full CDK redeploy
 * 4. Less complex CI/CD pipeline
 */

/**
 * Future Enhancements for CDK Integration:
 *
 * 1. Support multiple GCP projects
 * 2. Add message filtering by dashboard/severity
 * 3. Implement dead-letter queue (SQS) for failed publishes
 * 4. Add Lambda@Edge for cross-region relay
 * 5. Support for multiple cloud targets (Azure, Google Cloud, etc.)
 * 6. Automatic retry logic with exponential backoff
 * 7. CloudWatch Dashboard for monitoring
 * 8. EventBridge rule for complex event routing
 *
 * Example (Future):
 *
 * interface RelayTarget {
 *   type: "gcp-pubsub" | "azure-eventhub" | "eventbridge-cross-region";
 *   config: Record<string, string>;
 * }
 *
 * export interface RelayStackProps extends cdk.StackProps {
 *   dashboardStateTable: dynamodb.Table;
 *   targets: RelayTarget[];
 * }
 *
 * // Then instantiate per target
 */
