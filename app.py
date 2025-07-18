#!/usr/bin/env python3
import os

import aws_cdk as cdk
from aws_cdk import Environment

# Importing stacks
from stacks.network.vpc import VpcStack
from stacks.network.security_group import SecurityGroupStack
from stacks.network.alb import ALBStack
from stacks.storage.ecr import ECRStack
from stacks.storage.rds import RDSStack
from stacks.storage.s3 import S3BucketStack
from stacks.compute.ecs import ECSStack
from stacks.ci_cd.pipeline import CodePipelineStack

app = cdk.App()

cidr = app.node.try_get_context("vpc_cidr") or os.getenv("VPC_CIDR") or "10.0.0.0/16"
max_azs = app.node.try_get_context("max_azs") or int(os.getenv("MAX_AZS") or 2)

# VPC Stack
vpc_stack = VpcStack(app, "VPCStack","configs/network/vpc.yaml")

# Security Group Stack
security_group_stack = SecurityGroupStack(app, "SecurityGroupStack", "configs/network/security_group.yaml")

# ALB Stack
alb_stack = ALBStack(
	app, "ALBStack",
	"configs/network/alb.yaml",
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

# RDS Stack
rds_stack = RDSStack(
	app, "RDSStack",
	"configs/storage/rds.yaml")

# S3 Bucket Stack
s3_bucket_stack = S3BucketStack(
	app, "S3BucketStack",
	"configs/storage/s3.yaml",
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

# ECR Stack
ecr_stack = ECRStack(app, "ECRStack",
	config_path="configs/storage/ecr.yaml",
	env=Environment(
		account=os.getenv('CDK_DEFAULT_ACCOUNT'),
		region=os.getenv('CDK_DEFAULT_REGION'))
)

# ECS Stack
ecs_stack = ECSStack(
	app, "ECSStack",
	config_path="configs/compute/ecs.yaml",
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

# CodePipeline Stack
code_pipeline_backend_stack = CodePipelineStack(
	app, "CodePipelineStack",
	config_path="configs/ci_cd/pipeline.yaml",
	env=Environment(
		account=os.getenv("CDK_DEFAULT_ACCOUNT"),
		region=os.getenv("CDK_DEFAULT_REGION")
	)
)

app.synth()
