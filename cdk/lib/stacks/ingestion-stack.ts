import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import * as path from "path";

export interface IngestionStackProps extends cdk.StackProps {
  signalsTable: dynamodb.Table;
  eventBus: events.EventBus;
}

export class IngestionStack extends cdk.Stack {
  public readonly functions: lambda.Function[] = [];

  constructor(scope: Construct, id: string, props: IngestionStackProps) {
    super(scope, id, props);

    const { signalsTable, eventBus } = props;

    // === LAMBDA INGESTION FUNCTIONS ===

    // 1. FRED Poller (Federal Reserve Economic Data)
    const fredFunction = this.createLambdaFunction(
      "FredPoller",
      "fred-poller",
      "fred_poller.handler",
      {
        SIGNALS_TABLE: signalsTable.tableName,
        EVENT_BUS_NAME: eventBus.eventBusName,
        FRED_API_KEY: process.env.FRED_API_KEY || "PLACEHOLDER_FRED_KEY",
      },
      signalsTable,
      eventBus
    );
    this.functions.push(fredFunction);

    // 2. Trends Poller (Google Trends via pytrends)
    const trendsFunction = this.createLambdaFunction(
      "TrendsPoller",
      "trends-poller",
      "trends_poller.handler",
      {
        SIGNALS_TABLE: signalsTable.tableName,
        EVENT_BUS_NAME: eventBus.eventBusName,
        TRENDS_KEYWORDS: "economic inequality,wealth gap,income distribution",
      },
      signalsTable,
      eventBus,
      512
    );
    this.functions.push(trendsFunction);

    // 3. Finnhub Connector
    const finnhubFunction = this.createLambdaFunction(
      "FinnhubConnector",
      "finnhub-connector",
      "finnhub_connector.handler",
      {
        SIGNALS_TABLE: signalsTable.tableName,
        EVENT_BUS_NAME: eventBus.eventBusName,
        FINNHUB_API_KEY: process.env.FINNHUB_API_KEY || "PLACEHOLDER_FINNHUB_KEY",
      },
      signalsTable,
      eventBus
    );
    this.functions.push(finnhubFunction);

    // 4. GDELT Querier (Global Database of Events, Language and Tone)
    const gdeltFunction = this.createLambdaFunction(
      "GdeltQuerier",
      "gdelt-querier",
      "gdelt_querier.handler",
      {
        SIGNALS_TABLE: signalsTable.tableName,
        EVENT_BUS_NAME: eventBus.eventBusName,
        GDELT_BIGQUERY_PROJECT: process.env.GCP_PROJECT_ID || "mvt-observer",
      },
      signalsTable,
      eventBus,
      512
    );
    this.functions.push(gdeltFunction);

    // 5. World Bank Poller
    const worldbankFunction = this.createLambdaFunction(
      "WorldbankPoller",
      "worldbank-poller",
      "worldbank_poller.handler",
      {
        SIGNALS_TABLE: signalsTable.tableName,
        EVENT_BUS_NAME: eventBus.eventBusName,
        WORLDBANK_INDICATORS: "SI.POV.GINI,NY.GDP.PCAP,SP.URB.TOTL.IN.ZS",
      },
      signalsTable,
      eventBus
    );
    this.functions.push(worldbankFunction);

    // 6. YFinance Streamer (VIX, FX, ETFs)
    const yfinanceFunction = this.createLambdaFunction(
      "YfinanceStreamer",
      "yfinance-streamer",
      "yfinance_streamer.handler",
      {
        SIGNALS_TABLE: signalsTable.tableName,
        EVENT_BUS_NAME: eventBus.eventBusName,
        YFINANCE_SYMBOLS: "^VIX,EURUSD=X,GLD,TLT,EEM",
      },
      signalsTable,
      eventBus
    );
    this.functions.push(yfinanceFunction);

    // Outputs
    new cdk.CfnOutput(this, "IngestionFunctionsCount", {
      value: String(this.functions.length),
    });
  }

  /**
   * Helper to create a Lambda function with DynamoDB and EventBridge permissions
   */
  private createLambdaFunction(
    constructId: string,
    functionName: string,
    handler: string,
    environment: Record<string, string>,
    signalsTable: dynamodb.Table,
    eventBus: events.EventBus,
    memory: number = 256
  ): lambda.Function {
    const lambdaFunction = new lambda.Function(this, constructId, {
      functionName: `mvt-${functionName}`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler,
      code: lambda.Code.fromAsset(
        path.join(__dirname, `../handlers/ingestion/${functionName}`)
      ),
      memorySize: memory,
      timeout: this.getTimeoutForFunction(functionName),
      environment,
    });

    // Grant DynamoDB write access to signals table
    signalsTable.grantWriteData(lambdaFunction);

    // Grant EventBridge put-events access
    eventBus.grantPutEventsTo(lambdaFunction);

    return lambdaFunction;
  }

  /**
   * Get timeout based on function type
   */
  private getTimeoutForFunction(functionName: string): cdk.Duration {
    switch (functionName) {
      case "trends-poller":
      case "gdelt-querier":
        return cdk.Duration.seconds(60);
      default:
        return cdk.Duration.seconds(30);
    }
  }

  /**
   * Get or create SSM parameter for API keys
   */
  private getOrCreateParameter(parameterName: string, defaultValue: string): string {
    try {
      return ssm.StringParameter.valueFromLookup(this, parameterName);
    } catch {
      // If parameter doesn't exist, return placeholder
      // (In production, parameters should be pre-created)
      return defaultValue;
    }
  }
}
