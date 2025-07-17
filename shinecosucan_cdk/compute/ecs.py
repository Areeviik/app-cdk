from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
	aws_ecs as ecs,
	aws_iam as iam,
	aws_logs as logs,
	aws_elasticloadbalancingv2 as elb,
	aws_secretsmanager as secretsmanager,
	aws_autoscaling as autoscaling
)
from constructs import Construct

class ECSStack(Stack):
	def __init__(
			self, scope: Construct, construct_id: str,
			vpc: ec2.Vpc,
			frontend_tg: elb.ApplicationTargetGroup,
			backend_tg: elb.ApplicationTargetGroup,
			backend_image: str,
			frontend_image: str,
			frontend_sg: ec2.SecurityGroup,
			backend_sg: ec2.SecurityGroup,
			**kwargs
	) -> None:
		super().__init__(scope, construct_id, **kwargs)

		prj_name = self.node.try_get_context("project_name")
		env_name = self.node.try_get_context("env")

		self.cluster = ecs.Cluster(
			self, "Cluster",
			cluster_name=f"{prj_name}-{env_name}-ecs-cluster",
			vpc=vpc,
		)

		auto_scaling_group = autoscaling.AutoScalingGroup(
			self, "ECSAutoScalingGroup",
			vpc=vpc,
			instance_type=ec2.InstanceType("t3.small"),
			machine_image=ecs.EcsOptimizedImage.amazon_linux2(),
			min_capacity=1,
			max_capacity=4,
			desired_capacity=2,
			associate_public_ip_address=True,
			vpc_subnets=ec2.SubnetSelection(
				subnet_type=ec2.SubnetType.PUBLIC
			),
			security_group=backend_sg
		)

		capacity_provider = ecs.AsgCapacityProvider(
			self, "ECSCapacityProvider",
			auto_scaling_group=auto_scaling_group,
			capacity_provider_name=f"{prj_name}-{env_name}-capacity-provider",
		)
		self.cluster.add_asg_capacity_provider(capacity_provider)

		ec2_role = iam.Role(
			self, "EC2InstanceRole",
			assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
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

		frontend_task_definition = ecs.TaskDefinition(
			self, "FrontendTaskDefinition",
			compatibility=ecs.Compatibility.EC2,
			network_mode=ecs.NetworkMode.BRIDGE,
			execution_role=execution_role,
			cpu="512",
			memory_mib="916"
		)

		frontend_task_definition.add_container(
			"Frontend",
			image=ecs.ContainerImage.from_registry(frontend_image),
			memory_limit_mib=None,
			cpu=0,
			command=["sh", "-c", "HOST=0.0.0.0 yarn dev"],
			environment={
				"VENDURE_API_URL": "https://shinecosucan-admin.dev.yospace.ai/shop-api",
				"PORT": "8002",
				"HOST": "0.0.0.0"
			},
			logging=ecs.LogDriver.aws_logs(
				stream_prefix="frontend",
				log_group=logs.LogGroup(
					self, "FrontendLogGroup",
					log_group_name=f"{prj_name}-{env_name}-frontend-logs"
				)
			),
			port_mappings=[
				ecs.PortMapping(
					container_port=8002,
					host_port=8002,
					protocol=ecs.Protocol.TCP
				)
			]		)

		rds_secret = secretsmanager.Secret.from_secret_name_v2(
			self, "RDSSecret", f"{prj_name}/{env_name}/rds-credentials"
		)

		backend_task_definition = ecs.TaskDefinition(
			self, "BackendTaskDefinition",
			compatibility=ecs.Compatibility.EC2,
			network_mode=ecs.NetworkMode.BRIDGE,
			execution_role=execution_role,
			cpu="512",
			memory_mib="512"
		)
		backend_task_definition.add_container(
			"Backend",
			image=ecs.ContainerImage.from_registry(backend_image),
			memory_limit_mib=None,
			cpu=0,
			essential=True,
			command=["npm", "run", "start:server"],
			environment={
				"SUPERADMIN_PASSWORD": "superadmin",
				"APP_ENV": "dev",
				"DB_USERNAME": "postgres",
				"PORT": "3000",
				"DB_PORT": "5432",
				"SUPERADMIN_USERNAME": "superadmin",
				"DB_NAME": "vendure",
				"ADMIN_API_HOST": "https://shinecosucan-admin.dev.yospace.ai",
				"ADMIN_API_PORT": "443",
				"COOKIE_SECRET": "5J6d2Llv0zkXsucalhIVnw",
			},
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
			port_mappings=[ecs.PortMapping(container_port=3000, host_port=3000, protocol=ecs.Protocol.TCP)],
		)
		backend_task_definition.add_container(
			"Worker",
			image=ecs.ContainerImage.from_registry(backend_image),
			essential=False,
			command=["npm", "run", "start:worker"],
			cpu=0,
			environment={
				"SUPERADMIN_PASSWORD": "superadmin",
				"APP_ENV": "dev",
				"DB_USERNAME": "postgres",
				"DB_PORT": "5432",
				"SUPERADMIN_USERNAME": "superadmin",
				"DB_NAME": "vendure",
				"ADMIN_API_HOST": "https://shinecosucan-admin.dev.yospace.ai",
				"ADMIN_API_PORT": "443",
				"COOKIE_SECRET": "5J6d2Llv0zkXsucalhIVnw",
			},
			secrets={
				"DB_HOST": ecs.Secret.from_secrets_manager(rds_secret, "host"),
				"DB_PASSWORD": ecs.Secret.from_secrets_manager(rds_secret, "password"),
			},
			logging=ecs.LogDriver.aws_logs(
				stream_prefix="ecs",
				log_group=logs.LogGroup.from_log_group_name(
					self, "WorkerLogGroup", "/ecs/shinecosucan-test-ecs-tf-backend"
				)
			),
		)

		self.frontend_service = ecs.CfnService(
			self, "FrontendService",
			cluster=self.cluster.cluster_name,
			service_name=f"{prj_name}-{env_name}-frontend-service",
			task_definition=frontend_task_definition.task_definition_arn,
			launch_type="EC2",
			scheduling_strategy="DAEMON",
			load_balancers=[ecs.CfnService.LoadBalancerProperty(
				target_group_arn=frontend_tg.target_group_arn,
				container_name="Frontend",
				container_port=8002
			)]
		)

		self.backend_service = ecs.CfnService(
			self, "BackendService",
			cluster=self.cluster.cluster_name,
			service_name=f"{prj_name}-{env_name}-backend-service",
			task_definition=backend_task_definition.task_definition_arn,
			launch_type="EC2",
			scheduling_strategy="DAEMON",
			load_balancers=[ecs.CfnService.LoadBalancerProperty(
				target_group_arn=backend_tg.target_group_arn,
				container_name="Backend",
				container_port=3000
			)]
		)
