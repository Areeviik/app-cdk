#!/usr/bin/env python3
import os

import aws_cdk as cdk
from aws_cdk import Environment

from shinecosucan_cdk.vpc import VpcStack

app = cdk.App()

cidr = app.node.try_get_context("vpc_cidr") or os.getenv("VPC_CIDR") or "10.0.0.0/16"
max_azs = app.node.try_get_context("max_azs") or int(os.getenv("MAX_AZS") or 2)

vpc_stack = VpcStack(app, "VpcStack",
				cidr=cidr,
				max_azs=max_azs,
				env=Environment(
					account=os.getenv('CDK_DEFAULT_ACCOUNT'),
					region=os.getenv('CDK_DEFAULT_REGION'))
)

app.synth()
