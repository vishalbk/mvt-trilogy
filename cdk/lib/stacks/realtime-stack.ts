import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as apigatewayv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as apigatewayv2_integrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import * as path from "path";

export interface RealtimeStackProps extends cdk.StackProps {
  connectionsTable: dynamodb.Table;
  dashboardStateTable: dynamodb.Table;
  eventBus: events.EventBus;
}

export class RealtimeStack extends cdk.Stack {
  public readonly websocketApi: apigatewayv2.WebSocketApi;
  public readonly functions: lambda.Function[] = [];

  constructor(scope: Construct, id: string, props: RealtimeStackProps) {
    super(scope, id, props);

    const { connectionsTable, dashboardStateTable, eventBus } = props;

    // === WEBSOCKET API GATEWAY ===

    this.websocketApi = new apigatewayv2.WebSocketApi(this, "MVTWebSocketApi", {
      apiName: "mvt-realtime-ws",
      routeSelectionExpression: "$request.body.action",
    });

    const stage = new apigatewayv2.WebSocketStage(this, "MVTProdStage", {
      webSocketApi: this.websocketApi,
      stageName: "prod",
      autoDeploy: true,
    });

    // === LAMBDA HANDLERS ===

    // 1. $connect Handler
    const wsConnectFunction = new lambda.Function(this, "WsConnectHandler", {
      functionName: "mvt-ws-connect",
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: "ws_connect.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../handlers/realtime/ws-connect")
      ),
      memorySize: 128,
      timeout: cdk.Duration.seconds(10),
      environment: {
        CONNECTIONS_TABLE: connectionsTable.tableName,
      },
    });
    connectionsTable.grantWriteData(wsConnectFunction);
    this.functions.push(wsConnectFunction);

    // 2. $disconnect Handler
    const wsDisconnectFunction = new lambda.Function(this, "WsDisconnectHandler", {
      functionName: "mvt-ws-disconnect",
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: "ws_disconnect.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../handlers/realtime/ws-disconnect")
      ),
      memorySize: 128,
      timeout: cdk.Duration.seconds(10),
      environment: {
        CONNECTIONS_TABLE: connectionsTable.tableName,
      },
    });
    connectionsTable.grantReadWriteData(wsDisconnectFunction);
    this.functions.push(wsDisconnectFunction);

    // 3. Broadcast Handler (triggered by DynamoDB Stream)
    const wsBroadcastFunction = new lambda.Function(this, "WsBroadcastHandler", {
      functionName: "mvt-ws-broadcast",
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: "ws_broadcast.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../handlers/realtime/ws-broadcast")
      ),
      memorySize: 256,
      timeout: cdk.Duration.seconds(15),
      environment: {
        CONNECTIONS_TABLE: connectionsTable.tableName,
        WEBSOCKET_ENDPOINT: stage.url.replace('wss://', 'https://'),
      },
    });

    // Grant broadcast function permissions to:
    // - Read connections from DynamoDB
    connectionsTable.grantReadData(wsBroadcastFunction);

    // - Call API Gateway Management API to send messages to clients
    const apiManagementPolicy = new iam.PolicyStatement({
      actions: [
        "execute-api:ManageConnections",
        "execute-api:InvalidateCache",
      ],
      resources: [
        `arn:aws:execute-api:${this.region}:${this.account}:${this.websocketApi.apiId}/*`,
      ],
    });
    wsBroadcastFunction.addToRolePolicy(apiManagementPolicy);

    this.functions.push(wsBroadcastFunction);

    // === ATTACH HANDLERS TO WEBSOCKET ROUTES ===

    // $connect route
    this.websocketApi.addRoute("$connect", {
      integration: new apigatewayv2_integrations.WebSocketLambdaIntegration(
        "ConnectIntegration",
        wsConnectFunction
      ),
    });

    // $disconnect route
    this.websocketApi.addRoute("$disconnect", {
      integration: new apigatewayv2_integrations.WebSocketLambdaIntegration(
        "DisconnectIntegration",
        wsDisconnectFunction
      ),
    });

    // $default route (for routing other messages)
    this.websocketApi.addRoute("$default", {
      integration: new apigatewayv2_integrations.WebSocketLambdaIntegration(
        "DefaultIntegration",
        wsConnectFunction // Route to a handler that processes generic messages
      ),
    });

    // sendmessage custom route
    this.websocketApi.addRoute("sendmessage", {
      integration: new apigatewayv2_integrations.WebSocketLambdaIntegration(
        "SendMessageIntegration",
        wsConnectFunction // Route custom messages appropriately
      ),
    });

    // === ATTACH BROADCAST HANDLER TO DYNAMODB STREAM ===

    // Add DynamoDB Stream as event source for broadcast function
    wsBroadcastFunction.addEventSource(
      new cdk.aws_lambda_event_sources.DynamoEventSource(dashboardStateTable, {
        startingPosition: lambda.StartingPosition.LATEST,
        batchSize: 10,
        parallelizationFactor: 2,
      })
    );

    // === OUTPUTS ===

    new cdk.CfnOutput(this, "WebSocketApiId", {
      value: this.websocketApi.apiId,
    });

    new cdk.CfnOutput(this, "WebSocketApiEndpoint", {
      value: stage.url,
      description: "WebSocket API endpoint for client connections",
    });

    new cdk.CfnOutput(this, "WebSocketApiArn", {
      value: `arn:aws:execute-api:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:${this.websocketApi.apiId}`,
    });

    new cdk.CfnOutput(this, "RealtimeFunctionsCount", {
      value: String(this.functions.length),
    });
  }
}
