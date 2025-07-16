#!/usr/bin/env python3
import os
from pickletools import read_decimalnl_short

import aws_cdk as cdk
from aws_cdk import Environment

from shinecosucan_cdk.network.vpc import VpcStack
from shinecosucan_cdk.network.security_group import SecurityGroupStack
from shinecosucan_cdk.network.alb import ALBStack
from shinecosucan_cdk.storage.ecr import ECRStack
from shinecosucan_cdk.storage.rds import RDSStack

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

app.synth()
