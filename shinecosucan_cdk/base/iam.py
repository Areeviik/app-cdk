from aws_cdk import(
	Stack,
	aws_iam as iam,
)
from constructs import Construct

class IAMStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		codebuild_policy = iam.ManagedPolicy(
			self, "CodeBuildServiceRolePolicy",
			managed_policy_name="CodeBuildServiceRolePolicy",
			statements=[
				iam.PolicyStatement(
					sid="CloudWatchLogsPolicy",
					effect=iam.Effect.ALLOW,
					actions=[
						"logs:CreateLogGroup",
						"logs:CreateLogStream",
						"logs:PutLogEvents"
					],
					resources=["*"]
				),
				iam.PolicyStatement(
					sid="CodeCommitPolicy",
					effect=iam.Effect.ALLOW,
					actions=["codecommit:GitPull"],
					resources=["*"]
				),
				iam.PolicyStatement(
					sid="S3GetObjectPolicy",
					effect=iam.Effect.ALLOW,
					actions=["s3:GetObject", "s3:GetObjectVersion"],
					resources=["*"]
				),
				iam.PolicyStatement(
					sid="S3PutObjectPolicy",
					effect=iam.Effect.ALLOW,
					actions=["s3:PutObject"],
					resources=["*"]
				),
				iam.PolicyStatement(
					sid="ECRPullPolicy",
					effect=iam.Effect.ALLOW,
					actions=[
						"ecr:BatchCheckLayerAvailability",
						"ecr:GetDownloadUrlForLayer",
						"ecr:BatchGetImage"
					],
					resources=["*"]
				),
				iam.PolicyStatement(
					sid="ECRAuthPolicy",
					effect=iam.Effect.ALLOW,
					actions=["ecr:GetAuthorizationToken"],
					resources=["*"]
				),
				iam.PolicyStatement(
					sid="S3BucketIdentity",
					effect=iam.Effect.ALLOW,
					actions=["s3:GetBucketAcl", "s3:GetBucketLocation"],
					resources=["*"]
				),
			]
		)

		self.codebuild_role = iam.Role(
			self, "CodeBuildServiceRole",
			assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
			role_name="CodeBuildServiceRole",
			managed_policies=[
				iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryPowerUser"),
				codebuild_policy
			]
		)