from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    Tags
)
from constructs import Construct
from utils.yaml_loader import load_yaml
from utils.ssm import get_ssm_parameter, put_ssm_parameter
from typing import Dict, Any

class SecurityGroupStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config_path: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.config = load_yaml(config_path)
        self.prj_name = self.config["project_name"]
        self.env_name = self.config["env"]

        self.security_groups: Dict[str, ec2.SecurityGroup] = {}
        self.vpcs: Dict[str, ec2.IVpc] = {}

        self._create_security_groups()

    def _create_security_groups(self):
        sg_configs = self.config.get("security_groups", [])

        if not sg_configs:
            print(f"WARNING: No security group configurations found in the YAML file for {self.stack_name}.")
            return

        for sg_conf in sg_configs:
            self._create_base_security_group(sg_conf)

        for sg_conf in sg_configs:
            self._add_rules_to_security_group(sg_conf)

    def _create_base_security_group(self, sg_conf: Dict[str, Any]):
        sg_name_in_config = sg_conf["name"]
        vpc_key = sg_conf["vpc"]
        description = sg_conf.get("description", f"Security group for {sg_name_in_config}")
        allow_all_outbound = sg_conf.get("allow_all_outbound", True)

        if vpc_key not in self.vpcs:
            vpc_id_param_name = f"/{self.prj_name}/{self.env_name}/vpc/{vpc_key}"
            try:
                vpc_id = get_ssm_parameter(self, vpc_id_param_name)
                self.vpcs[vpc_key] = ec2.Vpc.from_vpc_attributes(
                    self, f"{vpc_key.capitalize()}VpcImport",
                    vpc_id=vpc_id,
                    availability_zones=sg_conf.get("availability_zones")
                )
            except Exception as e:
                raise ValueError(
                    f"Failed to import VPC '{vpc_key}' (SSM parameter '{vpc_id_param_name}'): {e}")

        vpc = self.vpcs[vpc_key]

        sg_logical_id = f"{self.prj_name}-{self.env_name}-{sg_name_in_config}"
        cdk_resource_name = f"{self.prj_name}-{self.env_name}-{sg_name_in_config}-sg"

        sg = ec2.SecurityGroup(
            self,
            sg_logical_id,
            security_group_name=cdk_resource_name,
            vpc=vpc,
            description=description,
            allow_all_outbound=allow_all_outbound
        )

        Tags.of(sg).add("Project", self.prj_name)
        Tags.of(sg).add("Environment", self.env_name)
        Tags.of(sg).add("Name", cdk_resource_name)
        Tags.of(sg).add("SgNameInConfig", sg_name_in_config)

        self.security_groups[sg_name_in_config] = sg

        put_ssm_parameter(self, f"/{self.prj_name}/{self.env_name}/sg/{sg_name_in_config}", sg.security_group_id)

    def _add_rules_to_security_group(self, sg_conf: Dict[str, Any]):
        sg_name_in_config = sg_conf["name"]
        sg = self.security_groups[sg_name_in_config]

        for rule_conf in sg_conf.get("ingress", []):
            try:
                peer = self._resolve_peer(rule_conf)
                port = self._resolve_port_range(rule_conf)
                description = rule_conf.get("description", "Ingress rule from config")
                sg.add_ingress_rule(peer, port, description)
            except ValueError as e:
                print(f"ERROR: Failed to add ingress rule to '{sg_name_in_config}': {e}. Rule config: {rule_conf}")
                continue
            except Exception as e:
                print(f"UNEXPECTED ERROR: Adding ingress rule to '{sg_name_in_config}': {e}. Rule config: {rule_conf}")
                continue

        if not sg_conf.get("allow_all_outbound", True):
            for rule_conf in sg_conf.get("egress", []):
                try:
                    peer = self._resolve_peer(rule_conf)
                    port = self._resolve_port_range(rule_conf)
                    description = rule_conf.get("description", "Egress rule from config")
                    sg.add_egress_rule(peer, port, description)
                except ValueError as e:
                    print(f"ERROR: Failed to add egress rule to '{sg_name_in_config}': {e}. Rule config: {rule_conf}")
                    continue
                except Exception as e:
                    print(f"UNEXPECTED ERROR: Adding egress rule to '{sg_name_in_config}': {e}. Rule config: {rule_conf}")
                    continue

    def _resolve_peer(self, rule: Dict[str, Any]) -> ec2.IPeer:
        if "cidr" in rule:
            return ec2.Peer.ipv4(rule["cidr"])
        elif "source_sg" in rule:
            source_sg_name = rule["source_sg"]
            if source_sg_name not in self.security_groups:
                imported_sg_id_param = f"/{self.prj_name}/{self.env_name}/sg/{source_sg_name}"
                try:
                    imported_sg_id = get_ssm_parameter(self, imported_sg_id_param)
                    imported_sg = ec2.SecurityGroup.from_security_group_id(
                        self, f"ImportedSg-{source_sg_name}", imported_sg_id
                    )
                    self.security_groups[source_sg_name] = imported_sg
                    return imported_sg
                except Exception as e:
                    raise ValueError(
                        f"Security group '{source_sg_name}' (SSM: {imported_sg_id_param})  or could not be imported for peer reference: {e}")
            return self.security_groups[source_sg_name]
        elif "prefix_list_id" in rule:
            return ec2.Peer.prefix_list(rule["prefix_list_id"])
        elif "connection_peer_id" in rule:
            return ec2.Peer.vpc_peer(rule["connection_peer_id"])
        elif "all_ipv4" in rule and rule["all_ipv4"]:
            return ec2.Peer.any_ipv4()
        elif "all_ipv6" in rule and rule["all_ipv6"]:
            return ec2.Peer.any_ipv6()
        else:
            raise ValueError(
                "Ingress/Egress rule must include 'cidr', 'source_sg', 'prefix_list_id', 'connection_peer_id', 'all_ipv4', or 'all_ipv6'.")

    def _resolve_port_range(self, rule: Dict[str, Any]) -> ec2.Port:
        protocol = rule.get("protocol", "tcp").lower()
        if "port" in rule:
            port = rule["port"]
            if port == -1 or protocol == "all":
                return ec2.Port.all_traffic()
            elif protocol == "tcp":
                return ec2.Port.tcp(port)
            elif protocol == "udp":
                return ec2.Port.udp(port)
            elif protocol == "icmp":
                return ec2.Port.icmp(port.get("type"), port.get("code"))
            else:
                raise ValueError(f"Unsupported protocol '{protocol}' for single port in rule: {rule}")
        elif "from_port" in rule and "to_port" in rule:
            from_port = rule["from_port"]
            to_port = rule["to_port"]
            if protocol == "tcp":
                return ec2.Port.tcp_range(from_port, to_port)
            elif protocol == "udp":
                return ec2.Port.udp_range(from_port, to_port)
            else:
                raise ValueError(f"Unsupported protocol '{protocol}' for port range in rule: {rule}")
        elif protocol == "all_traffic":
            return ec2.Port.all_traffic()
        else:
            raise ValueError(
                "Ingress/Egress rule must specify either 'port' or 'from_port'/'to_port', or 'protocol: all_traffic'.")
