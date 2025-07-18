from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_ssm as ssm
)
from constructs import Construct
from utils.yaml_loader import load_yaml

class VpcStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str,
        config_path: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        config = load_yaml(config_path)

        env_name = config["env"]
        prj_name = config["project_name"]
        vpc_configs = config.get("vpcs", [])

        self.vpcs = {}

        for vpc_cfg in vpc_configs:
            vpc_name = vpc_cfg["name"]
            cidr = vpc_cfg.get("cidr", "10.0.0.0/16")
            max_azs = vpc_cfg.get("max_azs", 2)

            subnet_configs = []
            for subnet in vpc_cfg.get("subnets", []):
                subnet_type = getattr(ec2.SubnetType, subnet["type"])
                subnet_configs.append(ec2.SubnetConfiguration(
                    name=subnet["name"],
                    subnet_type=subnet_type,
                    cidr_mask=subnet["cidr_mask"]
                ))

            vpc_id = f"{prj_name}-{env_name}-vpc-{vpc_name}"

            vpc = ec2.Vpc(
                self,
                vpc_id,
                ip_addresses=ec2.IpAddresses.cidr(cidr),
                max_azs=max_azs,
                subnet_configuration=subnet_configs
            )

            self.vpcs[vpc_name] = vpc

            ssm.StringParameter(
                self,
                f"{vpc_id}-VpcIdParam",
                string_value=vpc.vpc_id,
                parameter_name=f"/{prj_name}/{env_name}/vpc/{vpc_name}"
            )

            priv_subs = [subnet.subnet_id for subnet in vpc.private_subnets]
            for i, ps in enumerate(priv_subs, 1):
                ssm.StringParameter(
                    self,
                    f"{vpc_id}-PrivateSubnet{i}Param",
                    string_value=ps,
                    parameter_name=f"{prj_name}/{env_name}/subnet/private-{i}"
                )
