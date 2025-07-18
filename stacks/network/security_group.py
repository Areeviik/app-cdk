from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_ssm as ssm
)
from constructs import Construct
from utils.yaml_loader import load_yaml
from utils.ssm import get_ssm_parameter

def create_security_group(scope: Construct, sg_id: str, name: str, vpc: ec2.IVpc, description: str) -> ec2.SecurityGroup:
    return ec2.SecurityGroup(
        scope,
        sg_id,
        security_group_name=name,
        vpc=vpc,
        description=description,
        allow_all_outbound=True
    )

class SecurityGroupStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config_path: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        config = load_yaml(config_path)
        prj_name = config["project_name"]
        env_name = config["env"]

        vpc_param_name = f"/{prj_name}/{env_name}/vpc/{config['vpc']}"
        vpc_id = get_ssm_parameter(self, vpc_param_name)
        vpc = ec2.Vpc.from_vpc_attributes(self, "Vpc",
            vpc_id=vpc_id,
            availability_zones=[az for az in config.get("availability_zones")],
        )

        self.security_groups = {}

        for sg_conf in config.get("security_groups", []):
            sg_name = sg_conf["name"]
            sg_id = f"{prj_name}-{env_name}-{sg_name}-sg"
            description = sg_conf.get("description", "")

            sg = create_security_group(
                self,
                sg_id,
                f"{prj_name}-{env_name}-{sg_name}",
                vpc,
                description
            )

            self.security_groups[sg_name] = sg

            for rule in sg_conf.get("ingress", []):
                peer = self._resolve_peer(rule)
                port = self._resolve_port(rule)
                description = rule.get("description", "")
                sg.add_ingress_rule(peer, port, description)

            ssm.StringParameter(self, f"{sg_name}SGParam",
                parameter_name=f"/{prj_name}/{env_name}/sg/{sg_name}",
                string_value=sg.security_group_id
            )

    def _resolve_peer(self, rule: dict) -> ec2.IPeer:
        if "cidr" in rule:
            return ec2.Peer.ipv4(rule["cidr"])
        elif "source_sg" in rule:
            source_sg_name = rule["source_sg"]
            if source_sg_name not in self.security_groups:
                raise ValueError(f"Security group '{source_sg_name}' not found for source_sg reference.")
            return self.security_groups[source_sg_name]
        else:
            raise ValueError("Ingress rule must include either 'cidr' or 'source_sg'.")

    def _resolve_port(self, rule: dict) -> ec2.Port:
        port = rule["port"]
        protocol = rule.get("protocol", "tcp")

        if port == -1:
            return ec2.Port.all_traffic()
        elif protocol == "tcp":
            return ec2.Port.tcp(port)
        elif protocol == "udp":
            return ec2.Port.udp(port)
        else:
            raise ValueError(f"Unsupported protocol '{protocol}' in rule: {rule}")
