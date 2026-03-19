import {
  DynamoDBClient,
  ScanCommand,
  DeleteItemCommand
} from '@aws-sdk/client-dynamodb';
import { ApiGatewayManagementApiClient, PostToConnectionCommand } from '@aws-sdk/client-apigatewaymanagementapi';
import { DynamoDBStreamEvent, DynamoDBRecord } from 'aws-lambda';
import { unmarshall } from '@aws-sdk/util-dynamodb';

const dynamodb = new DynamoDBClient({ region: process.env.AWS_REGION });
const CONNECTIONS_TABLE = process.env.CONNECTIONS_TABLE!;
const WEBSOCKET_ENDPOINT = process.env.WEBSOCKET_ENDPOINT!;

interface DashboardStateItem {
  dashboard: string;
  panel: string;
  [key: string]: unknown;
}

interface ConnectionItem {
  connectionId: string;
  subscribedDashboards: string[];
}

const getApiGatewayClient = () => {
  return new ApiGatewayManagementApiClient({
    region: process.env.AWS_REGION,
    endpoint: WEBSOCKET_ENDPOINT
  });
};

const getActiveConnections = async (): Promise<ConnectionItem[]> => {
  try {
    const scanCommand = new ScanCommand({
      TableName: CONNECTIONS_TABLE
    });

    const response = await dynamodb.send(scanCommand);
    return (response.Items || []).map(item => {
      const unmarshalled = unmarshall(item) as ConnectionItem;
      return unmarshalled;
    });
  } catch (error) {
    console.error('Error scanning connections table:', error);
    return [];
  }
};

const deleteStaleConnection = async (connectionId: string): Promise<void> => {
  try {
    const deleteCommand = new DeleteItemCommand({
      TableName: CONNECTIONS_TABLE,
      Key: {
        connectionId: { S: connectionId }
      }
    });

    await dynamodb.send(deleteCommand);
    console.log(`Deleted stale connection: ${connectionId}`);
  } catch (error) {
    console.error(`Error deleting stale connection ${connectionId}:`, error);
  }
};

const broadcastToConnections = async (
  connections: ConnectionItem[],
  dashboard: string,
  messageData: unknown
): Promise<void> => {
  const apiGateway = getApiGatewayClient();

  const message = {
    type: 'dashboard_update',
    dashboard,
    data: messageData,
    timestamp: new Date().toISOString()
  };

  const messageStr = JSON.stringify(message);

  for (const connection of connections) {
    // Check if connection is subscribed to this dashboard
    if (!connection.subscribedDashboards.includes(dashboard)) {
      console.log(`Skipping connection ${connection.connectionId} - not subscribed to ${dashboard}`);
      continue;
    }

    try {
      const postCommand = new PostToConnectionCommand({
        ConnectionId: connection.connectionId,
        Data: messageStr
      });

      await apiGateway.send(postCommand);
      console.log(`Message sent to ${connection.connectionId}`);
    } catch (error: unknown) {
      const err = error as { name?: string; code?: string };

      // Handle stale connections
      if (err.name === 'GoneException' || err.code === 'GoneException') {
        console.log(`Connection ${connection.connectionId} is stale, removing...`);
        await deleteStaleConnection(connection.connectionId);
      } else {
        console.error(`Error sending message to ${connection.connectionId}:`, error);
      }
    }
  }
};

const processDynamoDBRecord = async (record: DynamoDBRecord): Promise<void> => {
  try {
    const eventName = record.eventName;

    if (eventName !== 'INSERT' && eventName !== 'MODIFY') {
      console.log(`Skipping ${eventName} event`);
      return;
    }

    const image = record.dynamodb?.NewImage;
    if (!image) {
      console.log('No NewImage in record');
      return;
    }

    const item = unmarshall(image) as DashboardStateItem;
    const dashboard = item.dashboard as string;
    const panel = item.panel as string;

    console.log(`Processing ${eventName} event for dashboard: ${dashboard}, panel: ${panel}`);

    // Get active connections
    const connections = await getActiveConnections();
    console.log(`Found ${connections.length} active connections`);

    if (connections.length === 0) {
      console.log('No active connections to broadcast to');
      return;
    }

    // Prepare message payload
    const messageData = {
      panel,
      ...item
    };

    // Broadcast to subscribed connections
    await broadcastToConnections(connections, dashboard, messageData);
  } catch (error) {
    console.error('Error processing DynamoDB record:', error);
    throw error;
  }
};

export const handler = async (event: DynamoDBStreamEvent): Promise<void> => {
  console.log('WebSocket broadcast handler triggered', JSON.stringify(event, null, 2));

  try {
    // Process each record from the DynamoDB Stream
    const records = event.Records || [];
    console.log(`Processing ${records.length} records`);

    await Promise.all(records.map(record => processDynamoDBRecord(record)));

    console.log('Broadcast completed');
  } catch (error) {
    console.error('Unhandled error in broadcast handler:', error);
    throw error;
  }
};
