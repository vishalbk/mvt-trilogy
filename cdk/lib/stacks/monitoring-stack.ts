import * as cdk from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sns from "aws-cdk-lib/aws-sns";
import * as sns_subscriptions from "aws-cdk-lib/aws-sns-subscriptions";
import { Construct } from "constructs";

export interface MonitoringStackProps extends cdk.StackProps {
  ingestionFunctions: lambda.Function[];
  processingFunctions: lambda.Function[];
  realtimeFunctions: lambda.Function[];
}

export class MonitoringStack extends cdk.Stack {
  public readonly dashboard: cloudwatch.Dashboard;
  public readonly alertTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: MonitoringStackProps) {
    super(scope, id, props);

    const { ingestionFunctions, processingFunctions, realtimeFunctions } = props;

    // === SNS TOPIC FOR ALERTS ===

    this.alertTopic = new sns.Topic(this, "MVTAlertTopic", {
      topicName: "mvt-alerts",
      displayName: "MVT Trilogy Alerts",
    });

    // === CLOUDWATCH DASHBOARD ===

    this.dashboard = new cloudwatch.Dashboard(this, "MVTObservatory", {
      dashboardName: "MVT-Observatory",
    });

    // Add dashboard title
    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: "# MVT Observatory - System Monitoring\n\nReal-time observability for the Macro Vulnerability Trilogy project",
        width: 24,
        height: 2,
      })
    );

    // === SECTION 1: LAMBDA INVOCATIONS ===

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: "## Lambda Invocations",
        width: 24,
        height: 1,
      })
    );

    const invocationWidgets = this.createFunctionMetricWidgets(
      "Invocations",
      "Invocations",
      cloudwatch.Statistic.SUM,
      [ingestionFunctions, processingFunctions, realtimeFunctions]
    );
    this.dashboard.addWidgets(...invocationWidgets);

    // === SECTION 2: LAMBDA ERRORS ===

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: "## Lambda Errors",
        width: 24,
        height: 1,
      })
    );

    const errorWidgets = this.createFunctionMetricWidgets(
      "Errors",
      "Errors",
      cloudwatch.Statistic.SUM,
      [ingestionFunctions, processingFunctions, realtimeFunctions]
    );
    this.dashboard.addWidgets(...errorWidgets);

    // === SECTION 3: LAMBDA DURATION ===

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: "## Lambda Duration (ms)",
        width: 24,
        height: 1,
      })
    );

    const durationWidgets = this.createFunctionMetricWidgets(
      "Duration",
      "Duration",
      cloudwatch.Statistic.AVERAGE,
      [ingestionFunctions, processingFunctions, realtimeFunctions]
    );
    this.dashboard.addWidgets(...durationWidgets);

    // === SECTION 4: DYNAMODB METRICS ===

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: "## DynamoDB Throttles",
        width: 24,
        height: 1,
      })
    );

    // DynamoDB throttle metrics (ConsumedWriteCapacityUnits, ConsumedReadCapacityUnits)
    const dynamodbThrottleWidget = new cloudwatch.GraphWidget({
      title: "DynamoDB Read/Write Throttle Events",
      left: [
        new cloudwatch.Metric({
          namespace: "AWS/DynamoDB",
          metricName: "ConsumedWriteCapacityUnits",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          dimensionsMap: {
            TableName: "mvt-signals",
          },
          label: "Signals Table - Write Capacity",
        }),
        new cloudwatch.Metric({
          namespace: "AWS/DynamoDB",
          metricName: "ConsumedReadCapacityUnits",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          dimensionsMap: {
            TableName: "mvt-signals",
          },
          label: "Signals Table - Read Capacity",
        }),
      ],
      width: 12,
      height: 6,
    });

    const dashboardStateThrottleWidget = new cloudwatch.GraphWidget({
      title: "Dashboard State Table Throttles",
      left: [
        new cloudwatch.Metric({
          namespace: "AWS/DynamoDB",
          metricName: "ConsumedWriteCapacityUnits",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          dimensionsMap: {
            TableName: "mvt-dashboard-state",
          },
          label: "Dashboard State - Write Capacity",
        }),
      ],
      width: 12,
      height: 6,
    });

    this.dashboard.addWidgets(dynamodbThrottleWidget, dashboardStateThrottleWidget);

    // === ALARMS ===

    // Lambda Error Rate Alarm (> 5% error rate)
    const errorRateAlarm = new cloudwatch.Alarm(this, "LambdaErrorRateAlarm", {
      alarmName: "mvt-lambda-error-rate-high",
      alarmDescription: "Alert when Lambda error rate exceeds 5%",
      metric: new cloudwatch.Metric({
        namespace: "AWS/Lambda",
        metricName: "Errors",
        statistic: cloudwatch.Statistic.SUM,
        period: cdk.Duration.minutes(5),
        region: this.region,
      }),
      threshold: 5, // Simplified: alert on any errors in the period
      evaluationPeriods: 2,
      datapointsToAlarm: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    errorRateAlarm.addAlarmAction(new cdk.aws_cloudwatch_actions.SnsAction(this.alertTopic));

    // DynamoDB Throttle Alarm (> 0 throttle events)
    const dynamodbThrottleAlarm = new cloudwatch.Alarm(
      this,
      "DynamoDBThrottleAlarm",
      {
        alarmName: "mvt-dynamodb-throttle-detected",
        alarmDescription: "Alert when DynamoDB throttling is detected",
        metric: new cloudwatch.Metric({
          namespace: "AWS/DynamoDB",
          metricName: "UserErrors",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(1),
          region: this.region,
        }),
        threshold: 0,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }
    );
    dynamodbThrottleAlarm.addAlarmAction(new cdk.aws_cloudwatch_actions.SnsAction(this.alertTopic));

    // Lambda Duration Alarm (> 20s average duration)
    const durationAlarm = new cloudwatch.Alarm(this, "LambdaDurationAlarm", {
      alarmName: "mvt-lambda-duration-high",
      alarmDescription: "Alert when average Lambda duration exceeds 20 seconds",
      metric: new cloudwatch.Metric({
        namespace: "AWS/Lambda",
        metricName: "Duration",
        statistic: cloudwatch.Statistic.AVERAGE,
        period: cdk.Duration.minutes(5),
        region: this.region,
      }),
      threshold: 20000, // 20 seconds in milliseconds
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    durationAlarm.addAlarmAction(new cdk.aws_cloudwatch_actions.SnsAction(this.alertTopic));

    // === CUSTOM METRIC WIDGETS FOR DETAILED FUNCTION MONITORING ===

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: "## Detailed Function Metrics",
        width: 24,
        height: 1,
      })
    );

    // Create a detailed breakdown by function group
    const ingestionMetricsWidget = new cloudwatch.GraphWidget({
      title: "Ingestion Functions - Invocations & Errors",
      left: ingestionFunctions.map((fn) =>
        new cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: "Invocations",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          dimensionsMap: {
            FunctionName: fn.functionName,
          },
          label: `${fn.functionName} - Invocations`,
        })
      ),
      right: ingestionFunctions.map((fn) =>
        new cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: "Errors",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          dimensionsMap: {
            FunctionName: fn.functionName,
          },
          label: `${fn.functionName} - Errors`,
        })
      ),
      width: 12,
      height: 6,
    });

    const processingMetricsWidget = new cloudwatch.GraphWidget({
      title: "Processing Functions - Invocations & Errors",
      left: processingFunctions.map((fn) =>
        new cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: "Invocations",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          dimensionsMap: {
            FunctionName: fn.functionName,
          },
          label: `${fn.functionName} - Invocations`,
        })
      ),
      right: processingFunctions.map((fn) =>
        new cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: "Errors",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          dimensionsMap: {
            FunctionName: fn.functionName,
          },
          label: `${fn.functionName} - Errors`,
        })
      ),
      width: 12,
      height: 6,
    });

    const realtimeMetricsWidget = new cloudwatch.GraphWidget({
      title: "Realtime Functions - Invocations & Errors",
      left: realtimeFunctions.map((fn) =>
        new cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: "Invocations",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          dimensionsMap: {
            FunctionName: fn.functionName,
          },
          label: `${fn.functionName} - Invocations`,
        })
      ),
      right: realtimeFunctions.map((fn) =>
        new cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: "Errors",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          dimensionsMap: {
            FunctionName: fn.functionName,
          },
          label: `${fn.functionName} - Errors`,
        })
      ),
      width: 12,
      height: 6,
    });

    this.dashboard.addWidgets(
      ingestionMetricsWidget,
      processingMetricsWidget,
      realtimeMetricsWidget
    );

    // === SECTION 5: API GATEWAY LATENCY (Sprint 4 Enhancement) ===

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: "## API Gateway & WebSocket Metrics",
        width: 24,
        height: 1,
      })
    );

    const apiGatewayLatencyWidget = new cloudwatch.GraphWidget({
      title: "WebSocket API - Connection Duration & Messages",
      left: [
        new cloudwatch.Metric({
          namespace: "AWS/ApiGateway",
          metricName: "IntegrationLatency",
          statistic: cloudwatch.Statistic.AVERAGE,
          period: cdk.Duration.minutes(5),
          region: this.region,
          label: "Integration Latency (avg)",
        }),
        new cloudwatch.Metric({
          namespace: "AWS/ApiGateway",
          metricName: "IntegrationLatency",
          statistic: "p99",
          period: cdk.Duration.minutes(5),
          region: this.region,
          label: "Integration Latency (p99)",
        }),
      ],
      right: [
        new cloudwatch.Metric({
          namespace: "AWS/ApiGateway",
          metricName: "MessageCount",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          label: "Message Count",
        }),
      ],
      width: 12,
      height: 6,
    });

    const apiGateway4xxWidget = new cloudwatch.GraphWidget({
      title: "API Gateway - 4xx/5xx Errors",
      left: [
        new cloudwatch.Metric({
          namespace: "AWS/ApiGateway",
          metricName: "4XXError",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          label: "4xx Errors",
        }),
        new cloudwatch.Metric({
          namespace: "AWS/ApiGateway",
          metricName: "5XXError",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          label: "5xx Errors",
        }),
      ],
      width: 12,
      height: 6,
    });

    this.dashboard.addWidgets(apiGatewayLatencyWidget, apiGateway4xxWidget);

    // === SECTION 6: LAMBDA CONCURRENT EXECUTIONS (Sprint 4) ===

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: "## Lambda Concurrent Executions & Throttles",
        width: 24,
        height: 1,
      })
    );

    const concurrentWidget = new cloudwatch.GraphWidget({
      title: "Account-Level Concurrent Executions",
      left: [
        new cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: "ConcurrentExecutions",
          statistic: cloudwatch.Statistic.MAXIMUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
          label: "Max Concurrent",
        }),
        new cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: "ConcurrentExecutions",
          statistic: cloudwatch.Statistic.AVERAGE,
          period: cdk.Duration.minutes(5),
          region: this.region,
          label: "Avg Concurrent",
        }),
      ],
      width: 12,
      height: 6,
    });

    const throttleWidget = new cloudwatch.GraphWidget({
      title: "Lambda Throttles by Function",
      left: [
        ...ingestionFunctions.map(
          (fn) =>
            new cloudwatch.Metric({
              namespace: "AWS/Lambda",
              metricName: "Throttles",
              statistic: cloudwatch.Statistic.SUM,
              period: cdk.Duration.minutes(5),
              region: this.region,
              dimensionsMap: { FunctionName: fn.functionName },
              label: fn.functionName,
            })
        ),
        ...processingFunctions.map(
          (fn) =>
            new cloudwatch.Metric({
              namespace: "AWS/Lambda",
              metricName: "Throttles",
              statistic: cloudwatch.Statistic.SUM,
              period: cdk.Duration.minutes(5),
              region: this.region,
              dimensionsMap: { FunctionName: fn.functionName },
              label: fn.functionName,
            })
        ),
      ],
      width: 12,
      height: 6,
    });

    this.dashboard.addWidgets(concurrentWidget, throttleWidget);

    // === SECTION 7: DYNAMODB DETAILED METRICS (Sprint 4) ===

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: "## DynamoDB Table Metrics",
        width: 24,
        height: 1,
      })
    );

    const tables = ["mvt-signals", "mvt-dashboard-state", "mvt-connections", "mvt-history"];
    const tableWidgets: cloudwatch.IWidget[] = tables.map(
      (tableName) =>
        new cloudwatch.GraphWidget({
          title: `${tableName} - R/W Capacity`,
          left: [
            new cloudwatch.Metric({
              namespace: "AWS/DynamoDB",
              metricName: "ConsumedReadCapacityUnits",
              statistic: cloudwatch.Statistic.SUM,
              period: cdk.Duration.minutes(5),
              region: this.region,
              dimensionsMap: { TableName: tableName },
              label: "Read Capacity",
            }),
            new cloudwatch.Metric({
              namespace: "AWS/DynamoDB",
              metricName: "ConsumedWriteCapacityUnits",
              statistic: cloudwatch.Statistic.SUM,
              period: cdk.Duration.minutes(5),
              region: this.region,
              dimensionsMap: { TableName: tableName },
              label: "Write Capacity",
            }),
          ],
          right: [
            new cloudwatch.Metric({
              namespace: "AWS/DynamoDB",
              metricName: "ThrottledRequests",
              statistic: cloudwatch.Statistic.SUM,
              period: cdk.Duration.minutes(5),
              region: this.region,
              dimensionsMap: { TableName: tableName },
              label: "Throttled",
            }),
          ],
          width: 12,
          height: 6,
        })
    );
    this.dashboard.addWidgets(...tableWidgets);

    // === SECTION 8: COST ESTIMATION (Sprint 4) ===

    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown:
          "## SRE Observatory Summary\n\n" +
          "**Cost Tracker** runs every hour, writing per-function costs to `mvt-dashboard-state`.\n" +
          "**Health Check** endpoint monitors all components.\n" +
          "**Performance Baseline** captures P50/P90/P99 per function.\n" +
          "**Capacity Planner** tracks DynamoDB utilization and concurrency.\n\n" +
          "View the SRE Observatory tab in the [MVT Dashboard](https://d2p9otbgwjwwuv.cloudfront.net) for detailed metrics.",
        width: 24,
        height: 3,
      })
    );

    // === ADDITIONAL ALARMS (Sprint 4) ===

    // WebSocket API 5xx alarm
    const wsAlarm = new cloudwatch.Alarm(this, "WebSocket5xxAlarm", {
      alarmName: "mvt-websocket-5xx-errors",
      alarmDescription: "Alert when WebSocket API has 5xx errors",
      metric: new cloudwatch.Metric({
        namespace: "AWS/ApiGateway",
        metricName: "5XXError",
        statistic: cloudwatch.Statistic.SUM,
        period: cdk.Duration.minutes(5),
        region: this.region,
      }),
      threshold: 10,
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    wsAlarm.addAlarmAction(
      new cdk.aws_cloudwatch_actions.SnsAction(this.alertTopic)
    );

    // DynamoDB System Errors alarm
    const dynamoSystemAlarm = new cloudwatch.Alarm(
      this,
      "DynamoDBSystemErrorAlarm",
      {
        alarmName: "mvt-dynamodb-system-errors",
        alarmDescription: "Alert when DynamoDB system errors occur",
        metric: new cloudwatch.Metric({
          namespace: "AWS/DynamoDB",
          metricName: "SystemErrors",
          statistic: cloudwatch.Statistic.SUM,
          period: cdk.Duration.minutes(5),
          region: this.region,
        }),
        threshold: 1,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }
    );
    dynamoSystemAlarm.addAlarmAction(
      new cdk.aws_cloudwatch_actions.SnsAction(this.alertTopic)
    );

    // === OUTPUTS ===

    new cdk.CfnOutput(this, "DashboardName", {
      value: this.dashboard.dashboardName,
      description: "CloudWatch Dashboard Name",
    });

    new cdk.CfnOutput(this, "AlertTopicArn", {
      value: this.alertTopic.topicArn,
      description: "SNS Topic ARN for monitoring alerts",
    });

    new cdk.CfnOutput(this, "DashboardUrl", {
      value: `https://console.aws.amazon.com/cloudwatch/home?region=${this.region}#dashboards:name=${this.dashboard.dashboardName}`,
      description: "Direct link to CloudWatch Dashboard",
    });
  }

  /**
   * Create metric widgets for Lambda functions grouped by type
   */
  private createFunctionMetricWidgets(
    metricName: string,
    metricLabel: string,
    statistic: cloudwatch.Statistic,
    functionGroups: lambda.Function[][]
  ): cloudwatch.IWidget[] {
    const widgets: cloudwatch.IWidget[] = [];
    const groupNames = ["Ingestion", "Processing", "Realtime"];

    functionGroups.forEach((functions, index) => {
      const widget = new cloudwatch.GraphWidget({
        title: `${groupNames[index]} - ${metricLabel}`,
        left: functions.map(
          (fn) =>
            new cloudwatch.Metric({
              namespace: "AWS/Lambda",
              metricName,
              statistic,
              period: cdk.Duration.minutes(5),
              region: this.region,
              dimensionsMap: {
                FunctionName: fn.functionName,
              },
              label: fn.functionName,
            })
        ),
        width: 8,
        height: 6,
      });
      widgets.push(widget);
    });

    return widgets;
  }
}
