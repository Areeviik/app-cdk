#!/usr/bin/env python3
import os

import aws_cdk as cdk
from aws_cdk import Environment

from shinecosucan_cdk.base.iam import IAMStack
from shinecosucan_cdk.network.vpc import VpcStack
from shinecosucan_cdk.network.security_group import SecurityGroupStack
from shinecosucan_cdk.network.alb import ALBStack
from shinecosucan_cdk.storage.ecr import ECRStack
from shinecosucan_cdk.storage.rds import RDSStack
from shinecosucan_cdk.storage.s3 import S3BucketStack
from shinecosucan_cdk.compute.ecs import ECSStack
from shinecosucan_cdk.ci_cd.backend_pipeline import CodePipelineBackendStack
from shinecosucan_cdk.ci_cd.frontend_pipeline import CodePipelineFrontendStack

app = cdk.App()

cidr = app.node.try_get_context("vpc_cidr") or os.getenv("VPC_CIDR") or "10.0.0.0/16"
max_azs = app.node.try_get_context("max_azs") or int(os.getenv("MAX_AZS") or 2)

# VPC Stack
vpc_stack = VpcStack(app, "VpcStack",
				cidr=cidr,
				max_azs=max_azs,
				env=Environment(
					account=os.getenv('CDK_DEFAULT_ACCOUNT'),
					region=os.getenv('CDK_DEFAULT_REGION'))
)

# Security Group Stack
security_group_stack = SecurityGroupStack(app, "SecurityGroupStack",
				vpc=vpc_stack.vpc,
				env=Environment(
					account=os.getenv('CDK_DEFAULT_ACCOUNT'),
					region=os.getenv('CDK_DEFAULT_REGION'))
)

# ECR Stack
ecr_stack = ECRStack(app, "ECRStack",
				repo_names=["frontend", "backend"],
				env=Environment(
					account=os.getenv('CDK_DEFAULT_ACCOUNT'),
					region=os.getenv('CDK_DEFAULT_REGION'))
)

# RDS Stack
rds_stack = RDSStack(app, "RDSStack",
				vpc=vpc_stack.vpc,
				db_sg=security_group_stack.db_sg,
				env=Environment(
					account=os.getenv('CDK_DEFAULT_ACCOUNT'),
					region=os.getenv('CDK_DEFAULT_REGION'))
)

# ALB Stack
alb_stack = ALBStack(
    app, "AlbStack",
    vpc=vpc_stack.vpc,
    alb_sg=security_group_stack.alb_sg,
    domain_name="dev.yospace.ai",
    frontend_subdomain="shinecosucan-app",
    backend_subdomain="shinecosucan-admin",
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

# ECS Stack
ecs_stack = ECSStack(
	app, "ECSStack",
	vpc=vpc_stack.vpc,
	frontend_tg=alb_stack.frontend_tg,
	backend_tg=alb_stack.backend_tg,
	backend_image=f"{ecr_stack.repositories['backend'].repository_uri}:1",                     # How to make this up to date?
	frontend_image=f"{ecr_stack.repositories['frontend'].repository_uri}:1",
	frontend_sg=security_group_stack.frontend_sg,
	backend_sg=security_group_stack.backend_sg,
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

# IAM Stack
iam_stack = IAMStack(
	app, "IAMStack",
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

# S3 Bucket Stack
s3_bucket_stack = S3BucketStack(
	app, "S3BucketStack",
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

# CodePipeline Backend Stack
code_pipeline_backend_stack = CodePipelineBackendStack(
	app, "CodePipelineBackendStack",
	artifactbucket=s3_bucket_stack.artifact_bucket,
	github_username="Areeviik",
	github_repo="app-backend",
	github_branch="main",
	vpc=vpc_stack.vpc,
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

# CodePipeline Frontend Stack
code_pipeline_frontend_stack = CodePipelineFrontendStack(
	app, "CodePipelineFrontendStack",
	artifactbucket=s3_bucket_stack.artifact_bucket,
	github_username="Areeviik",
	github_repo="app-frontend",
	github_branch="main",
	vpc=vpc_stack.vpc,
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

app.synth()
