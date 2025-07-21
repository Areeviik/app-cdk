from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
	aws_ecs as ecs,
	aws_iam as iam,
	aws_logs as logs,
	aws_elasticloadbalancingv2 as elb,
	aws_secretsmanager as secretsmanager,
	aws_autoscaling as autoscaling,
)
from constructs import Construct
from utils.yaml_loader import load_yaml
from utils.ssm import get_ssm_parameter, get_ssm_subnet_ids

class ECSStack(Stack):
	def __init__(
			self, scope: Construct,
			construct_id: str,
			config_path: str,
			**kwargs
	) -> None:
		super().__init__(scope, construct_id, **kwargs)

		config = load_yaml(config_path)
		prj_name = config["project_name"]
		env_name = config["env"]

		public_subnet_ids = get_ssm_subnet_ids(
			self, f"/{prj_name}/{env_name}/subnet/public", 2
		)

		vpc_param_name = f"/{prj_name}/{env_name}/vpc/{config['vpc']}"
		vpc_id = get_ssm_parameter(self, vpc_param_name)
		vpc = ec2.Vpc.from_vpc_attributes(
			self, "Vpc",
			vpc_id=vpc_id,
			availability_zones=[az for az in config.get("availability_zones")],
			public_subnet_ids=public_subnet_ids,
		)
		backend_sg_id = get_ssm_parameter(self, f"/{prj_name}/{env_name}/sg/backend-sg")

		backend_sg = ec2.SecurityGroup.from_security_group_id(self, "BackendSG", backend_sg_id)

		frontend_tg_arn = get_ssm_parameter(self, f"/{prj_name}/{env_name}/targetgroup/frontend")
		backend_tg_arn = get_ssm_parameter(self, f"/{prj_name}/{env_name}/targetgroup/backend")

		frontend_tg = elb.ApplicationTargetGroup.from_target_group_attributes(self, "FrontendTG", target_group_arn=frontend_tg_arn)
		backend_tg = elb.ApplicationTargetGroup.from_target_group_attributes(self, "BackendTG", target_group_arn=backend_tg_arn)

		self.cluster = ecs.Cluster(
			self,"ECSCluster",
			cluster_name=f"{prj_name}-{env_name}-ecs-cluster",
			vpc=vpc,
		)

		asg = autoscaling.AutoScalingGroup(
			self, "ECSAutoScalingGroup",
			vpc=vpc,
			instance_type=ec2.InstanceType(config.get("instance_type")),
			machine_image=ecs.EcsOptimizedImage.amazon_linux2(),
			min_capacity=config.get("asg_min_capacity"),
			max_capacity=config.get("asg_max_capacity"),
			desired_capacity=config.get("asg_desired_capacity"),
			associate_public_ip_address=True,
			vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
			security_group=backend_sg,
		)

		capacity_provider = ecs.AsgCapacityProvider(
			self, "ECSCapacityProvider",
			auto_scaling_group=asg,
			capacity_provider_name=f"{prj_name}-{env_name}-capacity-provider",
		)
		self.cluster.add_asg_capacity_provider(capacity_provider)

		ec2_role = iam.Role(
			self, "EC2InstanceRole",
			assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
			description="ECS Task Execution Role"
		)
		ec2_role.add_managed_policy(
			iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonEC2ContainerServiceforEC2Role")
		)
		execution_role = iam.Role(
			self, "ExecutionRole",
			assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
			description="ECS Task Execution Role"
		)
		execution_role.add_managed_policy(
			iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")
		)
		execution_role.add_managed_policy(
			iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly")
		)

		execution_role.add_to_policy(
			iam.PolicyStatement(
				actions=["secretsmanager:GetSecretValue"],
				resources=["*"]
			)
		)

		services = config["services"]
		self._create_frontend(prj_name, env_name, services["frontend"], frontend_tg, execution_role)
		self._create_backend(prj_name, env_name, services["backend"], backend_tg, execution_role)

	def _create_frontend(self, prj_name, env_name, config, tg, execution_role):
		frontend_task_definition = ecs.TaskDefinition(
			self, "FrontendTaskDefinition",
			compatibility=ecs.Compatibility.EC2,
			network_mode=ecs.NetworkMode.BRIDGE,
			execution_role=execution_role,
			cpu=str(config["cpu"]),
			memory_mib=config["memory_mib"]
		)

		frontend_task_definition.add_container(
			"Frontend",
			image=ecs.ContainerImage.from_registry(config["image"]),
			command=config.get("command"),
			environment=config.get("environment"),
			logging=ecs.LogDriver.aws_logs(
				stream_prefix="frontend",
				log_group=logs.LogGroup(
					self, "FrontendLogGroup",
					log_group_name=f"{prj_name}-{env_name}-frontend-logs"
				)
			),
			port_mappings=[ecs.PortMapping(
				container_port=config["port"],
				host_port=config["port"],
				protocol=ecs.Protocol.TCP
			)]
		)

		self.frontend_service = ecs.CfnService(
			self, "FrontendService",
			cluster=self.cluster.cluster_name,
			service_name=f"{prj_name}-{env_name}-frontend-service",
			task_definition=frontend_task_definition.task_definition_arn,
			launch_type="EC2",
			scheduling_strategy="DAEMON",
			load_balancers=[ecs.CfnService.LoadBalancerProperty(
				target_group_arn=tg.target_group_arn,
				container_name="Frontend",
				container_port=config["port"]
			)]
		)

	def _create_backend(self, prj_name, env_name, config, tg, execution_role):
		rds_secret = secretsmanager.Secret.from_secret_name_v2(
			self, "RDSSecret", f"{prj_name}/{env_name}/rds-credentials"
		)

		task_def = ecs.TaskDefinition(
			self, "BackendTaskDefinition",
			compatibility=ecs.Compatibility.EC2,
			network_mode=ecs.NetworkMode.BRIDGE,
			execution_role=execution_role,
			cpu=str(config["cpu"]),
			memory_mib=config["memory_mib"]
		)
		app_conf = config["containers"]["app"]

		task_def.add_container(
			"Backend",
			image=ecs.ContainerImage.from_registry(app_conf["image"]),
			command=["npm", "run", "start:server"],
			environment=config.get("environment"),
			secrets={
				"DB_HOST": ecs.Secret.from_secrets_manager(rds_secret, "host"),
				"DB_PASSWORD": ecs.Secret.from_secrets_manager(rds_secret, "password"),
			},
			logging=ecs.LogDriver.aws_logs(
				stream_prefix="backend",
				log_group=logs.LogGroup(
					self, "BackendLogGroup",
					log_group_name=f"{prj_name}-{env_name}-backend-logs"
				)
			),
			port_mappings=[ecs.PortMapping(
				container_port=config["port"],
				host_port=config["port"],
				protocol=ecs.Protocol.TCP
			)]
		)

		worker_conf = config["containers"]["worker"]
		task_def.add_container(
			"Worker",
			image=ecs.ContainerImage.from_registry(worker_conf["image"]),
			essential=False,
			command=["npm", "run", "start:worker"],
			environment=config.get("environment"),
			secrets={
				"DB_HOST": ecs.Secret.from_secrets_manager(rds_secret, "host"),
				"DB_PASSWORD": ecs.Secret.from_secrets_manager(rds_secret, "password"),
			},
			logging=ecs.LogDriver.aws_logs(
				stream_prefix="ecs",
				log_group=logs.LogGroup.from_log_group_name(
					self, "WorkerLogGroup", f"/ecs/{prj_name}-{env_name}-ecs-tf-backend"
				)
			)
		)

		self.backend_service = ecs.CfnService(
			self, "BackendService",
			cluster=self.cluster.cluster_name,
			service_name=f"{prj_name}-{env_name}-backend-service",
			task_definition=task_def.task_definition_arn,
			launch_type="EC2",
			scheduling_strategy="DAEMON",
			load_balancers=[ecs.CfnService.LoadBalancerProperty(
				target_group_arn=tg.target_group_arn,
				container_name="Backend",
				container_port=config["port"]
			)]
		)
