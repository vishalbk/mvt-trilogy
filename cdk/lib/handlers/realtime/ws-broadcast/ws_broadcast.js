const {
  DynamoDBClient,
  ScanCommand,
  DeleteItemCommand
} = require('@aws-sdk/client-dynamodb');
const { ApiGatewayManagementApiClient, PostToConnectionCommand } = require('@aws-sdk/client-apigatewaymanagementapi');
const { unmarshall } = require('@aws-sdk/util-dynamodb');

const dynamodb = new DynamoDBClient({ region: process.env.AWS_REGION });
const CONNECTIONS_TABLE = process.env.CONNECTIONS_TABLE;
const WEBSOCKET_ENDPOINT = process.env.WEBSOCKET_ENDPOINT;

const getApiGatewayClient = () => {
  return new ApiGatewayManagementApiClient({
    region: process.env.AWS_REGION,
    endpoint: WEBSOCKET_ENDPOINT
  });
};

const getActiveConnections = async () => {
  try {
    const scanCommand = new ScanCommand({
      TableName: CONNECTIONS_TABLE
    });

    const response = await dynamodb.send(scanCommand);
    return (response.Items || []).map(item => {
      const unmarshalled = unmarshall(item);
      return unmarshalled;
    });
  } catch (error) {
    console.error('Error scanning connections table:', error);
    return [];
  }
};

const deleteStaleConnection = async (connectionId) => {
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
  connections,
  dashboard,
  messageData
) => {
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
    // subscribedDashboards may be a Set (from DynamoDB SS type) or an Array
    const subs = connection.subscribedDashboards;
    const isSubscribed = subs instanceof Set ? subs.has(dashboard)
      : Array.isArray(subs) ? subs.includes(dashboard)
      : true; // If no subscription info, broadcast to all
    if (!isSubscribed) {
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
    } catch (error) {
      const err = error;

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

const processDynamoDBRecord = async (record) => {
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

    const item = unmarshall(image);
    const dashboard = item.dashboard;
    const panel = item.panel;

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

exports.handler = async (event) => {
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
