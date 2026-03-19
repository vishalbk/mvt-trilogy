import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";

export interface FrontendStackProps extends cdk.StackProps {
  websocketApi: any; // apigatewayv2.WebSocketApi
}

export class FrontendStack extends cdk.Stack {
  public readonly bucket: s3.Bucket;
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    const { websocketApi } = props;

    // === S3 BUCKET FOR SPA ===
    this.bucket = new s3.Bucket(this, "MVTFrontendBucket", {
      bucketName: `mvt-frontend-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: false,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // === CLOUDFRONT DISTRIBUTION ===
    // Using OAI (Origin Access Identity) for broad CDK compatibility
    const oai = new cloudfront.OriginAccessIdentity(this, "S3OAI", {
      comment: "OAI for MVT frontend S3 bucket",
    });

    this.bucket.grantRead(oai);

    this.distribution = new cloudfront.Distribution(this, "MVTDistribution", {
      defaultBehavior: {
        origin: new origins.S3Origin(this.bucket, {
          originAccessIdentity: oai,
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        compress: true,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
      },
      defaultRootObject: "index.html",
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      comment: "MVT Observatory Frontend Distribution",
      enabled: true,
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
          ttl: cdk.Duration.seconds(0),
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
          ttl: cdk.Duration.seconds(0),
        },
      ],
    });

    // === OUTPUTS ===
    new cdk.CfnOutput(this, "BucketName", {
      value: this.bucket.bucketName,
      description: "S3 bucket name for frontend assets",
    });

    new cdk.CfnOutput(this, "DistributionDomainName", {
      value: this.distribution.domainName,
      description: "CloudFront distribution domain name",
    });

    new cdk.CfnOutput(this, "DistributionId", {
      value: this.distribution.distributionId,
      description: "CloudFront distribution ID",
    });

    new cdk.CfnOutput(this, "FrontendUrl", {
      value: `https://${this.distribution.domainName}`,
      description: "Frontend application URL",
    });

    if (websocketApi && websocketApi.apiId) {
      const wsEndpoint = `wss://${websocketApi.apiId}.execute-api.${this.region}.amazonaws.com/prod`;
      new cdk.CfnOutput(this, "WebSocketEndpoint", {
        value: wsEndpoint,
        description: "WebSocket endpoint for frontend to connect to",
      });
    }
  }
}
