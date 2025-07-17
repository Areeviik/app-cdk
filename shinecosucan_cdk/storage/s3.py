from aws_cdk import (
	Stack,
	aws_s3 as s3,
	RemovalPolicy
)
from constructs import Construct

class S3BucketStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		prj_name = self.node.try_get_context("project_name")
		env_name = self.node.try_get_context("env")

		self.artifact_bucket = s3.Bucket(
			self,
			"ArtifactBucket",
			access_control=s3.BucketAccessControl.BUCKET_OWNER_FULL_CONTROL,
			encryption=s3.BucketEncryption.S3_MANAGED,
			bucket_name=f"{prj_name}-{env_name}-artifacts",
			block_public_access=s3.BlockPublicAccess(
				block_public_acls=True,
				block_public_policy=True,
				ignore_public_acls=True,
				restrict_public_buckets=True
			),
			removal_policy=RemovalPolicy.DESTROY,
			auto_delete_objects=True
		)

		frontend_bucket = s3.Bucket(
			self,
			"FrontendBucket",
			access_control=s3.BucketAccessControl.BUCKET_OWNER_FULL_CONTROL,
			encryption=s3.BucketEncryption.S3_MANAGED,
			bucket_name=f"{prj_name}-{env_name}-frontend",
			block_public_access=s3.BlockPublicAccess(
				block_public_acls=True,
				block_public_policy=True,
				ignore_public_acls=True,
				restrict_public_buckets=True
			),
			removal_policy=RemovalPolicy.DESTROY,
			auto_delete_objects=True
		)