from ipaddress import ip_address

from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
	aws_ssm as ssm
)
from constructs import Construct

class VpcStack(Stack):
	def __init__(self, scope: Construct, construct_id:str, cidr: str = "10.0.0.0/16", max_azs: int = 2, **kwargs)->None:
		super().__init__(scope, construct_id, **kwargs)

		prj_name = self.node.try_get_context("project_name")
		env_name = self.node.try_get_context("env")

		self.vpc = ec2.Vpc(
			self,
			"shinecosucan-test-vpc",
			ip_addresses=ec2.IpAddresses.cidr(cidr),
			max_azs=max_azs,
			subnet_configuration=[
				ec2.SubnetConfiguration(
					name="Public",
					subnet_type=ec2.SubnetType.PUBLIC,
					cidr_mask=24
				),
				ec2.SubnetConfiguration(
					name="Private",
					subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
					cidr_mask=24
				)
			]
		)

		priv_subs = [subnet.subnet_id for subnet in self.vpc.private_subnets]

		for i, ps in enumerate(priv_subs, 1):
			ssm.StringParameter(
				self,
				f'private_subnet_{i}',
				string_value=ps,
				parameter_name=f'/{env_name}/private-subnet-{i}'
			)