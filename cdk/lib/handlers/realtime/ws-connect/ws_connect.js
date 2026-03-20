const { DynamoDBClient, PutItemCommand } = require('@aws-sdk/client-dynamodb');

const dynamodb = new DynamoDBClient({ region: process.env.AWS_REGION });

const CONNECTIONS_TABLE = process.env.CONNECTIONS_TABLE;

exports.handler = async (event) => {
  console.log('WebSocket $connect handler triggered', JSON.stringify(event, null, 2));

  try {
    const connectionId = event.requestContext.connectionId;

    // Parse subscribed dashboards from query parameters
    const queryParams = event.queryStringParameters || {};
    let subscribedDashboards = ['inequality_pulse', 'sentiment_seismic', 'sovereign_dominoes', 'composite'];

    if (queryParams.dashboards) {
      subscribedDashboards = queryParams.dashboards.split(',').map(d => d.trim());
      console.log(`Client subscribed to: ${subscribedDashboards.join(', ')}`);
    } else {
      console.log('Client subscribed to all dashboards (default)');
    }

    // Calculate TTL (24 hours from now)
    const ttl = Math.floor(Date.now() / 1000) + 24 * 60 * 60;

    // Write connection to DynamoDB
    const putCommand = new PutItemCommand({
      TableName: CONNECTIONS_TABLE,
      Item: {
        connectionId: { S: connectionId },
        subscribedDashboards: { SS: subscribedDashboards },
        connectedAt: { S: new Date().toISOString() },
        ttl: { N: ttl.toString() }
      }
    });

    await dynamodb.send(putCommand);
    console.log(`Connection ${connectionId} stored successfully`);

    return {
      statusCode: 200,
      body: JSON.stringify({
        message: 'Connected successfully',
        connectionId,
        subscribedDashboards
      })
    };
  } catch (error) {
    console.error('Error in $connect handler:', error);
    return {
      statusCode: 500,
      body: JSON.stringify({
        error: 'Failed to establish connection'
      })
    };
  }
};
