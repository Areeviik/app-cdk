#!/usr/bin/env python3
import os

import aws_cdk as cdk
from aws_cdk import Environment

from shinecosucan_cdk.vpc import VpcStack
from shinecosucan_cdk.security_group import SecurityGroupStack

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

app.synth()
