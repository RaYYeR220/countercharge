import { Aws, CfnOutput, Duration, RemovalPolicy, SecretValue } from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

/**
 * Countercharge foundation: the stateful, account-wide resources that the
 * rest of the system (Gateway targets, the Fargate agent service, seed
 * scripts) is built on top of.
 *
 * This construct is intentionally the *only* place that creates Cognito,
 * DynamoDB, S3, KMS and Secrets Manager resources for the project. Tool targets
 * (Lambda tool targets, the Fargate agent service, gateway targets/policies)
 * should reference the public properties here (`table`, `bucket`,
 * `signingKey`, `userPool`, `webClient`, `systemClient`, ...) rather than
 * looking resources up by name, and should add its own constructs alongside
 * this one in `cdk-stack.ts` instead of editing this file.
 */
export class CountercodeFoundation extends Construct {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolDomain: cognito.UserPoolDomain;
  public readonly webClient: cognito.UserPoolClient;
  public readonly systemClient: cognito.UserPoolClient;
  public readonly resourceServer: cognito.UserPoolResourceServer;

  public readonly table: dynamodb.Table;
  public readonly bucket: s3.Bucket;
  public readonly signingKey: kms.Key;

  public readonly modelKeysSecret: secretsmanager.Secret;
  public readonly systemClientSecret: secretsmanager.Secret;
  public readonly demoUsersSecret: secretsmanager.Secret;

  /**
   * Temporary Lambda backing the `probe` gateway target used to verify that
   * the Cognito `role` claim reaches Cedar as a principal tag. Tool targets
   * should delete this construct (and the matching `probe` gateway target +
   * policies in agentcore.json) once the real `engine`/`case`/`actions`
   * Lambda targets exist.
   */
  public readonly probeFunction: lambda.Function;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    // ---------------------------------------------------------------
    // Cognito
    // ---------------------------------------------------------------
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'cc-users',
      featurePlan: cognito.FeaturePlan.ESSENTIALS,
      selfSignUpEnabled: false,
      standardAttributes: {
        email: { required: true, mutable: true },
      },
      customAttributes: {
        role: new cognito.StringAttribute({ mutable: true, minLen: 1, maxLen: 32 }),
        org: new cognito.StringAttribute({ mutable: true, minLen: 1, maxLen: 64 }),
      },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const preTokenGenerationFn = new lambda.Function(this, 'PreTokenGenerationFn', {
      functionName: 'cc-pretoken-gen',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      timeout: Duration.seconds(5),
      code: lambda.Code.fromInline(`
import json


def handler(event, context):
    # Cognito Pre Token Generation (V2_0) trigger.
    #
    # Copies the custom:role / custom:org user attributes onto the ACCESS
    # token as top-level role / org claims so Gateway/Cedar can read them
    # via principal.getTag("role").
    #
    # NOTE: M2M client-credentials tokens (the cc-system app client) never
    # invoke this trigger at all -- Cognito only calls Pre Token Generation
    # for user-context flows (SRP/USER_PASSWORD_AUTH sign-in, refresh,
    # hosted UI). There is no user attribute set to read for a
    # client-credentials grant, so a role=system claim cannot be injected
    # here; production policies for the system client must instead key off
    # the token's client_id claim (or the countercharge/system scope),
    # which Cognito always includes on client-credentials access tokens.
    request = event.get("request", {}) or {}
    attrs = request.get("userAttributes", {}) or {}

    claims = {}
    role = attrs.get("custom:role")
    org = attrs.get("custom:org")
    if role:
        claims["role"] = role
    if org:
        claims["org"] = org

    if claims:
        event["response"] = {
            "claimsAndScopeOverrideDetails": {
                "accessTokenGeneration": {
                    "claimsToAddOrOverride": claims,
                }
            }
        }
    return event
`),
      description: 'Adds custom:role/custom:org user attributes as access-token claims (V2_0 event).',
    });

    this.userPool.addTrigger(
      cognito.UserPoolOperation.PRE_TOKEN_GENERATION_CONFIG,
      preTokenGenerationFn,
      cognito.LambdaVersion.V2_0
    );

    this.userPoolDomain = this.userPool.addDomain('Domain', {
      cognitoDomain: { domainPrefix: `cc-${Aws.ACCOUNT_ID}` },
    });

    this.webClient = this.userPool.addClient('WebClient', {
      userPoolClientName: 'cc-web',
      generateSecret: false,
      authFlows: { userPassword: true, userSrp: true },
      preventUserExistenceErrors: true,
    });

    this.resourceServer = this.userPool.addResourceServer('SystemResourceServer', {
      identifier: 'countercharge',
      userPoolResourceServerName: 'countercharge',
      scopes: [
        new cognito.ResourceServerScope({
          scopeName: 'system',
          scopeDescription: 'Machine-to-machine access for the countercharge backend',
        }),
      ],
    });
    const systemScope = cognito.OAuthScope.resourceServer(
      this.resourceServer,
      new cognito.ResourceServerScope({ scopeName: 'system', scopeDescription: 'system' })
    );

    this.systemClient = this.userPool.addClient('SystemClient', {
      userPoolClientName: 'cc-system',
      generateSecret: true,
      authFlows: {},
      oAuth: {
        flows: { clientCredentials: true },
        scopes: [systemScope],
      },
      preventUserExistenceErrors: true,
    });

    new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new CfnOutput(this, 'UserPoolDiscoveryUrl', {
      value: `https://cognito-idp.${Aws.REGION}.amazonaws.com/${this.userPool.userPoolId}/.well-known/openid-configuration`,
    });
    new CfnOutput(this, 'UserPoolDomain', {
      value: `${this.userPoolDomain.domainName}.auth.${Aws.REGION}.amazoncognito.com`,
    });
    new CfnOutput(this, 'WebClientId', { value: this.webClient.userPoolClientId });
    new CfnOutput(this, 'SystemClientId', { value: this.systemClient.userPoolClientId });

    // ---------------------------------------------------------------
    // DynamoDB
    // ---------------------------------------------------------------
    this.table = new dynamodb.Table(this, 'CasesTable', {
      tableName: 'cc-cases',
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: RemovalPolicy.RETAIN,
    });
    this.table.addGlobalSecondaryIndex({
      indexName: 'GSI1',
      partitionKey: { name: 'GSI1PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'GSI1SK', type: dynamodb.AttributeType.STRING },
    });
    new CfnOutput(this, 'TableName', { value: this.table.tableName });

    // ---------------------------------------------------------------
    // S3 (uploads/, evidence/, sessions/, refdata/ are logical prefixes;
    // S3 needs no resource for them)
    // ---------------------------------------------------------------
    this.bucket = new s3.Bucket(this, 'DataBucket', {
      bucketName: `cc-${Aws.ACCOUNT_ID}-data`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
      cors: [
        {
          allowedOrigins: ['*'],
          allowedMethods: [s3.HttpMethods.PUT, s3.HttpMethods.GET],
          allowedHeaders: ['*'],
          maxAge: 3000,
        },
      ],
    });
    new CfnOutput(this, 'BucketName', { value: this.bucket.bucketName });

    // ---------------------------------------------------------------
    // KMS (HMAC signing key for findings + approval tokens)
    // ---------------------------------------------------------------
    this.signingKey = new kms.Key(this, 'SigningKey', {
      keySpec: kms.KeySpec.HMAC_256,
      keyUsage: kms.KeyUsage.GENERATE_VERIFY_MAC,
      description: 'Countercharge HMAC-SHA-256 key for finding signatures and approval tokens',
      removalPolicy: RemovalPolicy.RETAIN,
    });
    this.signingKey.addAlias('cc-signing');
    new CfnOutput(this, 'SigningKeyId', { value: this.signingKey.keyId });
    new CfnOutput(this, 'SigningKeyArn', { value: this.signingKey.keyArn });

    // ---------------------------------------------------------------
    // Secrets Manager
    // ---------------------------------------------------------------
    this.modelKeysSecret = new secretsmanager.Secret(this, 'ModelKeysSecret', {
      secretName: 'cc/model-keys',
      description: 'Model provider API keys, filled by infra/scripts (never set from CDK).',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({
          VENICE_API_KEY: '',
          GEMINI_API_KEY: '',
          OPENROUTER_API_KEY: '',
        }),
        generateStringKey: '_seed',
      },
    });

    this.systemClientSecret = new secretsmanager.Secret(this, 'SystemClientSecretValue', {
      secretName: 'cc/system-client',
      description: 'cc-system Cognito app client id/secret for M2M token requests.',
      secretObjectValue: {
        client_id: SecretValue.unsafePlainText(this.systemClient.userPoolClientId),
        client_secret: this.systemClient.userPoolClientSecret,
        token_endpoint: SecretValue.unsafePlainText(
          `https://${this.userPoolDomain.domainName}.auth.${Aws.REGION}.amazoncognito.com/oauth2/token`
        ),
        scope: SecretValue.unsafePlainText('countercharge/system'),
      },
    });

    this.demoUsersSecret = new secretsmanager.Secret(this, 'DemoUsersSecret', {
      secretName: 'cc/demo-users',
      description: 'Demo Cognito user credentials for verification/e2e scripts (filled by infra/scripts).',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({}),
        generateStringKey: '_seed',
      },
    });

    // ---------------------------------------------------------------
    // Temporary probe Lambda (gateway/policy verification only -- removed once tool targets land)
    // ---------------------------------------------------------------
    this.probeFunction = new lambda.Function(this, 'ProbeFunction', {
      functionName: 'cc-probe-tools',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      timeout: Duration.seconds(5),
      code: lambda.Code.fromInline(`
import json


def handler(event, context):
    tool_name = None
    try:
        tool_name = context.client_context.custom.get("bedrockAgentCoreToolName")
    except Exception:
        tool_name = None
    if tool_name and "___" in tool_name:
        tool_name = tool_name.split("___")[-1]

    print(json.dumps({"receivedEvent": event, "toolName": tool_name}))

    if tool_name in ("probe_read", "probe_write"):
        return {"tool": tool_name, "x": event.get("x"), "status": "ok"}

    return {"error": f"unknown tool: {tool_name}"}
`),
      description: 'TEMPORARY: backs the probe gateway target used to verify Cognito role claim -> Cedar tag mapping.',
    });
    // The AgentCore Gateway execution role invokes this function; grant it
    // explicitly so the permission does not depend on the L3 construct's
    // own wiring.
    this.probeFunction.addPermission('AllowBedrockAgentCoreInvoke', {
      principal: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      sourceAccount: Aws.ACCOUNT_ID,
    });
    new CfnOutput(this, 'ProbeFunctionArn', { value: this.probeFunction.functionArn });
  }
}
