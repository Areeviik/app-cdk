from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
	aws_ecs as ecs,
	aws_iam as iam,
	aws_logs as logs,
	aws_elasticloadbalancingv2 as elb,
	aws_autoscaling as autoscaling,
	aws_secretsmanager as secretsmanager,
	Tags,
	Duration,
	RemovalPolicy
)
from aws_cdk.aws_elasticloadbalancingv2 import INetworkLoadBalancer
from constructs import Construct
from typing import Dict, Any, List, Optional
from utils.yaml_loader import load_yaml
from utils.ssm import get_ssm_parameter, get_ssm_subnet_ids

class ECSStack(Stack):
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

		self.vpcs: Dict[str, ec2.IVpc] = {}
		self.security_groups: Dict[str, ec2.ISecurityGroup] = {}
		self.ecs_clusters: Dict[str, ecs.Cluster] = {}
		self.task_execution_roles: Dict[str, iam.IRole] = {}
		self.task_roles: Dict[str, iam.IRole] = {}
		self.target_groups: Dict[str, elb.IApplicationTargetGroup] = {}

		self._create_ecs_resources()

	def _create_ecs_resources(self):
		cluster_configs = self.config.get("clusters", [])
		if not cluster_configs:
			print(f"WARNING: No ECS cluster configurations found in the YAML file for {self.stack_name}.")
			return

		for cluster_conf in cluster_configs:
			self._create_single_cluster_and_services(cluster_conf)

	def _create_single_cluster_and_services(self, cluster_conf: Dict[str, Any]):
		cluster_name_in_config = cluster_conf["name"]
		vpc_key = cluster_conf["vpc"]
		azs = cluster_conf.get("availability_zones", [])
		asg_subnet_type = cluster_conf.get("asg_subnet_type", "PUBLIC").upper()

		vpc = self._get_vpc(vpc_key, asg_subnet_type, azs)
		cluster_sgs = self._get_security_groups(cluster_name_in_config, cluster_conf.get("security_groups", []))

		cluster_logical_id = f"{cluster_name_in_config.capitalize()}Cluster"
		cluster = ecs.Cluster(
			self, cluster_logical_id,
			cluster_name=f"{self.prj_name}-{self.env_name}-{cluster_name_in_config}-cluster",
			vpc=vpc,
		)
		self.ecs_clusters[cluster_name_in_config] = cluster

		if cluster_conf.get("capacity_provider_type") == "ASG":
			asg_logical_id = f"{cluster_name_in_config}ASG"
			asg_instance_type = ec2.InstanceType(cluster_conf["instance_type"])
			asg_ami_type = cluster_conf.get("asg_ami_type", "AL2")
			asg_machine_image = ecs.EcsOptimizedImage.amazon_linux2() if asg_ami_type == "AL2" else ecs.EcsOptimizedImage.amazon_linux2_arm64()

			asg = autoscaling.AutoScalingGroup(
				self, asg_logical_id,
				vpc=vpc,
				instance_type=asg_instance_type,
				machine_image=asg_machine_image,
				min_capacity=cluster_conf["asg_min_capacity"],
				max_capacity=cluster_conf["asg_max_capacity"],
				desired_capacity=cluster_conf["asg_desired_capacity"],
				associate_public_ip_address=cluster_conf.get("asg_associate_public_ip", False),
				vpc_subnets=ec2.SubnetSelection(subnet_type=getattr(ec2.SubnetType, asg_subnet_type)),
				security_group=cluster_sgs[0] if cluster_sgs else None,
			)

			capacity_provider_logical_id = f"{cluster_name_in_config}CapacityProvider"
			capacity_provider = ecs.AsgCapacityProvider(
				self, capacity_provider_logical_id,
				auto_scaling_group=asg,
				capacity_provider_name=f"{self.prj_name}-{self.env_name}-{cluster_name_in_config}-cp",
			)
			cluster.add_asg_capacity_provider(capacity_provider)
			print(f"INFO: Added ASG capacity provider '{capacity_provider.capacity_provider_name}' to cluster '{cluster.cluster_name}'.")

		execution_role = self._create_execution_role(cluster_name_in_config,cluster_conf.get("task_execution_role", {}))
		task_role = self._create_task_role(cluster_name_in_config, cluster_conf.get("task_role", {}))

		Tags.of(cluster).add("Project", self.prj_name)
		Tags.of(cluster).add("Environment", self.env_name)
		Tags.of(cluster).add("Name", f"{self.prj_name}-{self.env_name}-{cluster_name_in_config}-cluster")

		if cluster_conf.get("capacity_provider_type") == "ASG":
			Tags.of(asg).add("Project", self.prj_name)
			Tags.of(asg).add("Environment", self.env_name)
			Tags.of(asg).add("Name", f"{self.prj_name}-{self.env_name}-{cluster_name_in_config}-asg")

		for svc_conf in cluster_conf.get("services", []):
			self._create_ecs_service(cluster, vpc, cluster_sgs, svc_conf, execution_role, task_role)

	def _create_ecs_service(
			self,
			cluster: ecs.Cluster,
			vpc: ec2.IVpc,
			cluster_sgs: List[ec2.ISecurityGroup],
			svc_conf: Dict[str, Any],
			execution_role: iam.IRole,
			task_role: iam.IRole
	):
		service_name_in_config = svc_conf["name"]
		compatibility_enum = getattr(ecs.Compatibility, svc_conf.get("compatibility", "EC2").upper())
		network_mode_enum = getattr(ecs.NetworkMode, svc_conf.get("network_mode", "BRIDGE").upper())
		cpu = svc_conf.get("task_cpu", 256)
		memory_mib = svc_conf.get("task_memory_mib", 512)

		task_logical_id = f"{service_name_in_config.capitalize()}TaskDef"
		task_def = ecs.TaskDefinition(
			self, task_logical_id,
			compatibility=compatibility_enum,
			network_mode=network_mode_enum,
			execution_role= execution_role,
			cpu=str(cpu),
			memory_mib=str(memory_mib),
		)

		containers_conf = svc_conf.get("containers")
		if not containers_conf:
			containers_conf = [{
				"name": service_name_in_config,
				"image": svc_conf["image"],
				"command": svc_conf.get("command"),
				"environment": svc_conf.get("environment"),
				"secrets": svc_conf.get("secrets"),
				"cpu": svc_conf.get("cpu"),
				"memory_limit_mib": svc_conf.get("memory_mib"),
				"port_mappings": svc_conf.get("port_mappings", []),
				"container_health_check": svc_conf.get("container_health_check"),
				"log_config": svc_conf.get("log_config"),
			}]

		for container_conf in containers_conf:
			self._add_container_to_task_definition(task_def, container_conf)

		service_logical_id = f"{service_name_in_config}Service"
		ecs_service_name = f"{self.prj_name}-{self.env_name}-{service_name_in_config}-service"

		service_sgs = self._get_security_groups(service_name_in_config, svc_conf.get("service_security_groups", []))
		if not service_sgs:
			service_sgs = cluster_sgs
			if not service_sgs:
				print(f"WARNING: No security groups provided for service {service_name_in_config}. Creating a default security group that allows all outbound traffic.")
				default_sg = ec2.SecurityGroup(self, f"{service_logical_id}DefaultSG", vpc=vpc,description=f"Default SG for {service_name_in_config}")
				default_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.all_traffic(),"Allow all inbound to default SG")
				service_sgs = [default_sg]

		service_subnets_selection: Optional[ec2.SubnetSelection] = None
		service_subnet_type_str = svc_conf.get("service_subnet_type")
		if service_subnet_type_str:
			service_subnets_selection = ec2.SubnetSelection(
				subnet_type=getattr(ec2.SubnetType, service_subnet_type_str.upper()))

		# service = ecs.CfnService(
		# 	self, service_logical_id,
		# 	cluster=cluster,
		# 	service_name=ecs_service_name,
		# 	task_definition=task_def,
		# 	launch_type=ecs.LaunchType.EC2 if compatibility == "EC2" else ecs.LaunchType.FARGATE,
		# 	scheduling_strategy=svc_conf.get("scheduling_strategy", "REPLICA").upper(),
		# 	desired_count=svc_conf.get("desired_count", 1),
		# 	min_healthy_percent=svc_conf.get("min_healthy_percent", 50),
		# 	max_healthy_percent=svc_conf.get("max_healthy_percent", 100),
		# )
		service = ecs.Ec2Service(
			self, service_logical_id,
			cluster=cluster,
			service_name=ecs_service_name,
			task_definition=task_def,
			min_healthy_percent=svc_conf.get("min_healthy_percent", 50),
			max_healthy_percent=svc_conf.get("max_healthy_percent", 100),

		)
		scheduling_strategy_from_config = svc_conf.get("scheduling_strategy", "REPLICA").upper()
		if scheduling_strategy_from_config:
			cfn_service = service.node.default_child
			cfn_service.add_property_override("SchedulingStrategy", scheduling_strategy_from_config)

		lb_integrations = svc_conf.get("load_balancer_integrations", [])
		for lb_conf in lb_integrations:
			self._add_load_balancer_to_service(service, lb_conf)

		auto_scaling_conf_for_service = svc_conf.get("auto_scaling_target_tracking")
		if auto_scaling_conf_for_service:
			self._add_service_auto_scaling(service, auto_scaling_conf_for_service)

		Tags.of(task_def).add("Project", self.prj_name)
		Tags.of(task_def).add("Environment", self.env_name)
		Tags.of(task_def).add("Service", service_name_in_config)
		Tags.of(service).add("Project", self.prj_name)
		Tags.of(service).add("Environment", self.env_name)
		Tags.of(service).add("Name", ecs_service_name)

	def _add_container_to_task_definition(self, task_def: ecs.TaskDefinition, container_conf: Dict[str, Any]):
		container_name = container_conf["name"]
		image_uri = container_conf["image"]

		container_secrets = {}
		if container_conf.get("secrets"):
			for env_key, secret_info in container_conf["secrets"].items():
				secret_id = f"Secret{container_name.capitalize()}{env_key.upper()}"
				secret = secretsmanager.Secret.from_secret_name_v2(
					self, secret_id, secret_info["secret_name"]
				)
				container_secrets[env_key] = ecs.Secret.from_secrets_manager(
					secret, field=secret_info.get("json_key")
				)

		log_group: Optional[logs.ILogGroup] = None
		log_config_conf = container_conf.get("log_config", {})
		if log_config_conf.get("enabled", True):  # Logs enabled by default
			log_group_logical_id = f"{container_name.capitalize()}LogGroup"
			log_group_name = f"{self.prj_name}-{self.env_name}-{container_name}-logs"
			log_group = logs.LogGroup(
				self, log_group_logical_id,
				log_group_name=log_group_name,
				retention=getattr(logs.RetentionDays, log_config_conf.get("retention_days", "ONE_MONTH").upper()),
				removal_policy=getattr(RemovalPolicy, log_config_conf.get("removal_policy", "DESTROY").upper()),
			)
		health_check_conf = container_conf.get("container_health_check", {})
		health_check_props = None
		if health_check_conf.get("enabled", False) and health_check_conf.get("command"):
			health_check_props = {
				"command": health_check_conf["command"],
				"interval": Duration.seconds(health_check_conf.get("interval_seconds", 30)),
				"timeout": Duration.seconds(health_check_conf.get("timeout_seconds", 5)),
				"retries": health_check_conf.get("retries", 3),
				"start_period": Duration.seconds(health_check_conf.get("start_period_seconds", 0))
			}
		container_props = {
			"image": ecs.ContainerImage.from_registry(image_uri),
			"command": container_conf.get("command"),
			"environment": container_conf.get("environment"),
			"secrets": container_secrets,
			"logging": ecs.LogDriver.aws_logs(
				stream_prefix=container_conf["name"],
				log_group=log_group
			),
			"health_check": health_check_props,
			"cpu": container_conf.get("cpu", 0),
		}
		if container_conf.get("memory_limit_mib") is not None:
			container_props["memory_limit_mib"] = container_conf["memory_limit_mib"]
		if container_conf.get("memory_reservation_mib") is not None:
			container_props["memory_reservation_mib"] = container_conf["memory_reservation_mib"]
		container = task_def.add_container(
			container_name,
			**container_props
		)

		for pm_conf in container_conf.get("port_mappings", []):
			container.add_port_mappings(ecs.PortMapping(
				container_port=pm_conf["container_port"],
				host_port=pm_conf.get("host_port", pm_conf["container_port"]),
				protocol=getattr(ecs.Protocol, pm_conf.get("protocol", "TCP").upper()),
			))

		return container

	def _get_vpc(self, vpc_key: str, subnet_type: str, azs: List[str]) -> ec2.IVpc:
		if vpc_key not in self.vpcs:
			vpc_id_param_name = f"/{self.prj_name}/{self.env_name}/vpc/{vpc_key}"
			ssm_subnet_base_path = f"/{self.prj_name}/{self.env_name}/{vpc_key}/subnet/{subnet_type.lower()}"

			try:
				vpc_id = get_ssm_parameter(self, vpc_id_param_name)
				required_subnet_count = len(azs) if azs else 2
				subnet_ids = get_ssm_subnet_ids(self, ssm_subnet_base_path, required_subnet_count)

				vpc_attributes = {
					"vpc_id": vpc_id,
					"availability_zones": azs,
				}
				if subnet_type.upper() == "PUBLIC":
					vpc_attributes["public_subnet_ids"] = subnet_ids
				elif subnet_type.upper() == "PRIVATE_WITH_EGRESS":
					vpc_attributes["private_subnet_ids"] = subnet_ids
				elif subnet_type.upper() == "PRIVATE_ISOLATED":
					vpc_attributes["isolated_subnet_ids"] = subnet_ids
				else:
					raise ValueError(f"Unsupported subnet type '{subnet_type}' for VPC import.")

				self.vpcs[vpc_key] = ec2.Vpc.from_vpc_attributes(
					self, f"{vpc_key.capitalize()}VpcImport", **vpc_attributes
				)
			except Exception as e:
				raise ValueError(
					f"Failed to import VPC '{vpc_key}' (SSM: {vpc_id_param_name}, Subnets: {ssm_subnet_base_path}): {e}")
		return self.vpcs[vpc_key]

	def _get_security_groups(self, cluster_name: str, sg_names: List[str]) -> List[ec2.ISecurityGroup]:
		sgs: List[ec2.ISecurityGroup] = []
		for sg_name in sg_names:
			if sg_name not in self.security_groups:
				sg_id_param_name = f"/{self.prj_name}/{self.env_name}/sg/{sg_name}"
				try:
					sg_id = get_ssm_parameter(self, sg_id_param_name)
					self.security_groups[sg_name] = ec2.SecurityGroup.from_security_group_id(
						self, f"{cluster_name.capitalize()}{sg_name.capitalize()}SGImport", sg_id
					)
				except Exception as e:
					raise ValueError(
						f"Failed to import Security Group '{sg_name}' (ID from SSM: {sg_id_param_name}): {e}")
			sgs.append(self.security_groups[sg_name])
		return sgs

	def _resolve_iam_policy_statements(self, policy_statements_config: List[Dict[str, Any]]) -> List[
		iam.PolicyStatement]:
		statements: List[iam.PolicyStatement] = []
		for stmt_conf in policy_statements_config:
			actions = stmt_conf.get("actions", [])
			resources = stmt_conf.get("resources", ["*"])
			effect_str = stmt_conf.get("effect", "ALLOW").upper()
			effect = getattr(iam.Effect, effect_str)
			principals = self._resolve_iam_principals(stmt_conf.get("principals", []))

			statements.append(iam.PolicyStatement(
				actions=actions,
				resources=resources,
				effect=effect,
				principals=principals,
				conditions=stmt_conf.get("conditions")
			))
		return statements

	def _resolve_iam_principals(self, principals_config: List[Dict[str, Any]]) -> List[iam.IPrincipal]:
		principals: List[iam.IPrincipal] = []
		for principal_conf in principals_config:
			principal_type = principal_conf["type"]
			if principal_type == "arn":
				principals.append(iam.ArnPrincipal(principal_conf["arn"]))
			elif principal_type == "account_root":
				principals.append(iam.AccountRootPrincipal())
			elif principal_type == "service":
				principals.append(iam.ServicePrincipal(principal_conf["service"]))
			elif principal_type == "federated":
				principals.append(iam.FederatedPrincipal(
					federated=principal_conf["federated"],
					conditions=principal_conf.get("conditions")
				))
			elif principal_type == "any_aws":
				principals.append(iam.AnyPrincipal())
			else:
				raise ValueError(f"Unsupported principal type: {principal_type}")
		return principals

	def _create_execution_role(self, cluster_name: str, role_conf: Dict[str, Any]) -> iam.IRole:
		role_id = f"{cluster_name.capitalize()}ExecutionRole"
		if role_id not in self.task_execution_roles:
			role = iam.Role(
				self, role_id,
				assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
				description=role_conf.get("description", "ECS Task Execution Role"),
				managed_policies=[
					iam.ManagedPolicy.from_aws_managed_policy_name(mp)
					for mp in role_conf.get("managed_policies", [])
				]
			)
			for policy_stmt_conf in role_conf.get("inline_policies", []):
				policy_statements = self._resolve_iam_policy_statements([policy_stmt_conf])
				for statement in policy_statements:
					role.add_to_policy(statement)
			self.task_execution_roles[role_id] = role
		return self.task_execution_roles[role_id]

	def _create_task_role(self, cluster_name: str, role_conf: Dict[str, Any]) -> iam.IRole:
		role_id = f"{cluster_name.capitalize()}TaskRole"
		if role_id not in self.task_roles:
			role = iam.Role(
				self, role_id,
				assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
				description=role_conf.get("description", "ECS Task Role for application"),
				managed_policies=[
					iam.ManagedPolicy.from_aws_managed_policy_name(mp)
					for mp in role_conf.get("managed_policies", [])
				]
			)
			for policy_stmt_conf in role_conf.get("inline_policies", []):
				policy_statements = self._resolve_iam_policy_statements([policy_stmt_conf])
				for statement in policy_statements:
					role.add_to_policy(statement)
			self.task_roles[role_id] = role
		return self.task_roles[role_id]

	def _add_load_balancer_to_service(self, service: ecs.Ec2Service, lb_conf: Dict[str, Any]):
		target_group_name = lb_conf["tg_name"]

		if target_group_name not in self.target_groups:
			tg_arn_param = f"/{self.prj_name}/{self.env_name}/targetgroup/{target_group_name}"
			try:
				tg_arn = get_ssm_parameter(self, tg_arn_param)
				target_group = elb.ApplicationTargetGroup.from_target_group_attributes(
					self, f"{target_group_name.capitalize()}TGImport", target_group_arn=tg_arn
				)
				self.target_groups[target_group_name] = target_group
			except Exception as e:
				raise ValueError(
					f"Failed to import Target Group '{target_group_name}' (ARN from SSM: {tg_arn_param}): {e}")
		else:
			target_group = self.target_groups[target_group_name]

		service.attach_to_application_target_group(target_group)
		print(f"INFO: Attached service '{service.service_name}' to target group '{target_group.target_group_arn}'.")

	def _add_service_auto_scaling(self, service: ecs.Ec2Service, auto_scaling_conf: Dict[str, Any]):
		metric_type = auto_scaling_conf["metric_type"].upper()
		target_value = auto_scaling_conf["target_value"]
		scale_in_cooldown_seconds = auto_scaling_conf.get("scale_in_cooldown_seconds", 300)
		scale_out_cooldown_seconds = auto_scaling_conf.get("scale_out_cooldown_seconds", 300)

		scalable_target = service.auto_scale_task_count(
			min_capacity=auto_scaling_conf.get("min_capacity", 1),
			max_capacity=auto_scaling_conf.get("max_capacity", 10),
		)
		service_name_in_config = service.node.id.replace("Service","")

		if metric_type == "CPU":
			scalable_target.scale_on_cpu_utilization(
				f"CpuScaling-{service_name_in_config}",
				target_utilization_percent=target_value,
				scale_in_cooldown=Duration.seconds(scale_in_cooldown_seconds),
				scale_out_cooldown=Duration.seconds(scale_out_cooldown_seconds),
			)
		elif metric_type == "MEMORY":
			scalable_target.scale_on_memory_utilization(
				f"MemoryScaling-{service_name_in_config}",
				target_utilization_percent=target_value,
				scale_in_cooldown=Duration.seconds(scale_in_cooldown_seconds),
				scale_out_cooldown=Duration.seconds(scale_out_cooldown_seconds),
			)
		elif metric_type == "ALB_REQUEST_COUNT_PER_TARGET":
			if not auto_scaling_conf.get("alb_target_group_arn_param"):
				raise ValueError("ALB_REQUEST_COUNT_PER_TARGET scaling requires 'alb_target_group_arn_param'.")

			alb_tg_arn = get_ssm_parameter(self, auto_scaling_conf["alb_target_group_arn_param"])
			alb_tg = elb.ApplicationTargetGroup.from_target_group_attributes(
				self, f"{service_name_in_config}AlbTgScalingImport", target_group_arn=alb_tg_arn
			)
			scalable_target.scale_on_request_count(
				f"AlbRequestScaling-{service_name_in_config}",
				target_requests_per_target=target_value,
				target_group=alb_tg,
				scale_in_cooldown=Duration.seconds(scale_in_cooldown_seconds),
				scale_out_cooldown=Duration.seconds(scale_out_cooldown_seconds),
			)
		else:
			print(
				f"WARNING: Unsupported auto scaling metric type: {metric_type}. Skipping auto scaling for service {service_name_in_config}.")
