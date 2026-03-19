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
