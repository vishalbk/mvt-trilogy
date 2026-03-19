import { DynamoDBClient, DeleteItemCommand } from '@aws-sdk/client-dynamodb';
import { APIGatewayProxyEvent, APIGatewayProxyResult } from 'aws-lambda';

const dynamodb = new DynamoDBClient({ region: process.env.AWS_REGION });

const CONNECTIONS_TABLE = process.env.CONNECTIONS_TABLE!;

interface DisconnectEvent extends APIGatewayProxyEvent {
  requestContext: {
    connectionId: string;
    [key: string]: unknown;
  };
}

export const handler = async (event: DisconnectEvent): Promise<APIGatewayProxyResult> => {
  console.log('WebSocket $disconnect handler triggered');

  try {
    const connectionId = event.requestContext.connectionId;

    // Delete connection from DynamoDB
    const deleteCommand = new DeleteItemCommand({
      TableName: CONNECTIONS_TABLE,
      Key: {
        connectionId: { S: connectionId }
      }
    });

    await dynamodb.send(deleteCommand);
    console.log(`Connection ${connectionId} deleted successfully`);

    return {
      statusCode: 200,
      body: JSON.stringify({
        message: 'Disconnected successfully'
      })
    };
  } catch (error) {
    console.error('Error in $disconnect handler:', error);
    return {
      statusCode: 500,
      body: JSON.stringify({
        error: 'Failed to disconnect'
      })
    };
  }
};
