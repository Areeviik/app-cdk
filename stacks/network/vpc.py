from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
	Tags
)
from constructs import Construct
from utils.yaml_loader import load_yaml
from utils.ssm import put_ssm_parameter
from typing import Dict, Any, List

class VpcStack(Stack):
	def __init__(
			self, scope: Construct, construct_id: str,
			config_path: str,
			**kwargs
	) -> None:
		super().__init__(scope, construct_id, **kwargs)

		self.config = load_yaml(config_path)

		self.env_name = self.config["env"]
		self.prj_name = self.config["project_name"]

		self.vpcs: Dict[str, ec2.Vpc] = {}

		self._create_vpcs()

	def _create_vpcs(self):
		vpc_configs = self.config.get("vpcs", [])

		if not vpc_configs:
			self.node.add_warning("No VPC configurations found in the YAML file.")
			return

		for vpc_cfg in vpc_configs:
			self._create_single_vpc(vpc_cfg)

	def _create_single_vpc(self, vpc_cfg: Dict[str, Any]):
		vpc_name_in_config = vpc_cfg["name"]
		cidr = vpc_cfg["cidr"]
		max_azs = vpc_cfg.get("max_azs")
		nat_gateways = vpc_cfg.get("nat_gateways", 0)
		enable_dns_hostnames = vpc_cfg.get("enable_dns_hostnames", True)
		enable_dns_support = vpc_cfg.get("enable_dns_support", True)

		# vpc_logical_id = f"{vpc_name_in_config.capitalize()}Vpc"
		vpc_logical_id = f"{self.prj_name}-{self.env_name}-vpc-{vpc_name_in_config}"
		cdk_resource_name = f"{self.prj_name}-{self.env_name}-{vpc_name_in_config}-vpc"

		subnet_configurations: List[ec2.SubnetConfiguration] = []
		for subnet in vpc_cfg.get("subnets", []):
			try:
				subnet_configurations.append(ec2.SubnetConfiguration(
					name=subnet["name"],
					subnet_type=getattr(ec2.SubnetType, subnet["type"].upper()),
					cidr_mask=subnet.get("cidr_mask")
				))
			except AttributeError:
				self.node.add_error(
					f"Invalid SubnetType '{subnet['type']}' for VPC '{vpc_name_in_config}'. Must be PUBLIC, PRIVATE_WITH_EGRESS, or PRIVATE_ISOLATED.")
				continue

		nat_gateway_provider_type = vpc_cfg.get("nat_gateway_provider_type")
		nat_gateway_provider = None
		if nat_gateway_provider_type == "gateway_vnet":
			nat_gateway_provider = ec2.NatProvider.gateway_vnet()
		elif nat_gateway_provider_type == "instance":
			self.node.add_warning(
				f"Using NatProvider.instance() for VPC '{vpc_name_in_config}' requires 'instance_type' and 'machine_image' config for more robust solution. Using default.")
			nat_gateway_provider = ec2.NatProvider.instance()

		vpc = ec2.Vpc(
			self,
			vpc_logical_id,
			ip_addresses=ec2.IpAddresses.cidr(cidr),
			max_azs=max_azs,
			subnet_configuration=subnet_configurations,
			nat_gateways=nat_gateways,
			nat_gateway_provider=nat_gateway_provider,
			enable_dns_hostnames=enable_dns_hostnames,
			enable_dns_support=enable_dns_support,
			vpc_name=cdk_resource_name,
		)

		Tags.of(vpc).add("Project", self.prj_name)
		Tags.of(vpc).add("Environment", self.env_name)
		Tags.of(vpc).add("Name", cdk_resource_name)
		Tags.of(vpc).add("VpcNameInConfig", vpc_name_in_config)

		self.vpcs[vpc_name_in_config] = vpc

		put_ssm_parameter(self, f"/{self.prj_name}/{self.env_name}/vpc/{vpc_name_in_config}", vpc.vpc_id)

		subnet_group_map = {
			ec2.SubnetType.PUBLIC: vpc.public_subnets,
			ec2.SubnetType.PRIVATE_WITH_EGRESS: vpc.private_subnets,
			ec2.SubnetType.PRIVATE_ISOLATED: vpc.isolated_subnets,
		}

		for subnet_type, subnets in subnet_group_map.items():
			if subnets:
				subnet_config_names = {cfg.subnet_type: cfg.name for cfg in subnet_configurations}
				subnet_group_name = subnet_config_names.get(subnet_type,subnet_type.name)

				for i, subnet in enumerate(subnets):
					ssm_path = f"/{self.prj_name}/{self.env_name}/{vpc_name_in_config}/subnet/{subnet_group_name.lower()}/{i}"
					put_ssm_parameter(self, ssm_path, subnet.subnet_id)
