from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
	aws_ecs as ecs,
	aws_iam as iam,
	aws_logs as logs,
	aws_elasticloadbalancingv2 as elb,
	aws_autoscaling as autoscaling,
	RemovalPolicy
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

		for cluster_conf in config.get("clusters", []):
			cluster_name = cluster_conf["name"]
			vpc_name = cluster_conf["vpc"]
			vpc_param_name = f"/{prj_name}/{env_name}/vpc/{vpc_name}"
			vpc_id = get_ssm_parameter(self, vpc_param_name)

			public_subnet_ids = get_ssm_subnet_ids(
				self, f"/{prj_name}/{env_name}/{vpc_name}/subnet/public", 2
			)

			vpc = ec2.Vpc.from_vpc_attributes(
				self, f"{cluster_name}Vpc",
				vpc_id=vpc_id,
				availability_zones=cluster_conf.get("availability_zones", []),
				public_subnet_ids=public_subnet_ids,
			)
			security_groups = []
			for sg_name in cluster_conf.get("security_groups", []):
				sg_id = get_ssm_parameter(self, f"/{prj_name}/{env_name}/sg/{sg_name}")
				security_groups.append(ec2.SecurityGroup.from_security_group_id(self, f"SG-{sg_name}", sg_id))

			cluster = ecs.Cluster(
				self,"ECSCluster",
				cluster_name=f"{prj_name}-{env_name}-{cluster_name}-cluster",
				vpc=vpc,
			)

			asg = autoscaling.AutoScalingGroup(
				self, f"{cluster_name}ASG",
				vpc=vpc,
				instance_type=ec2.InstanceType(cluster_conf["instance_type"]),
				machine_image=ecs.EcsOptimizedImage.amazon_linux2(),
				min_capacity=cluster_conf["asg_min_capacity"],
				max_capacity=cluster_conf["asg_max_capacity"],
				desired_capacity=cluster_conf["asg_desired_capacity"],
				associate_public_ip_address=True,
				vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
				security_group=security_groups[0] if security_groups else None
			)

			capacity_provider = ecs.AsgCapacityProvider(
				self, f"{cluster_name}CapacityProvider",
				auto_scaling_group=asg,
				capacity_provider_name=f"{prj_name}-{env_name}-{cluster_name}-capacity-provider",
			)
			cluster.add_asg_capacity_provider(capacity_provider)

			ec2_role = iam.Role(
				self, f"{cluster_name}EC2InstanceRole",
				assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
				description="ECS Task Execution Role"
			)
			ec2_role.add_managed_policy(
				iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonEC2ContainerServiceforEC2Role")
			)
			execution_role = iam.Role(
				self, f"{cluster_name}ExecutionRole",
				assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
				description="ECS Task Execution Role"
			)
			execution_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy"))
			execution_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly"))
			execution_role.add_to_policy(iam.PolicyStatement(
				actions=["secretsmanager:GetSecretValue"],
				resources=["*"]
			))

			for svc_conf in cluster_conf.get("services", []):
				self._create_service(prj_name, env_name, cluster, svc_conf, execution_role)

	def _create_service(self, prj_name, env_name, cluster, config, execution_role):
		service_name = config["name"]
		containers = config.get("containers") or [
			{
				"name": service_name,
				"image": config["image"],
				"command": config.get("command"),
				"environment": config.get("environment")
			}
		]

		task_def = ecs.TaskDefinition(
			self, f"{service_name}TaskDef",
			compatibility=ecs.Compatibility.EC2,
			network_mode=ecs.NetworkMode.BRIDGE,
			execution_role=execution_role,
			cpu=str(config["cpu"]),
			memory_mib=config["memory_mib"]
		)
		for container_index, container_conf in enumerate(containers):
			container = task_def.add_container(
				container_conf["name"],
				image=ecs.ContainerImage.from_registry(container_conf["image"]),
				command=container_conf.get("command"),
				environment=container_conf.get("environment"),
				logging=ecs.LogDriver.aws_logs(
					stream_prefix=container_conf["name"],
					log_group=logs.LogGroup(
						self, f"{container_conf["name"]}LogGroup",
						log_group_name=f"{prj_name}-{env_name}-{container_conf['name']}-logs",
						removal_policy = RemovalPolicy.DESTROY
			)
				),
				port_mappings=[ecs.PortMapping(
					container_port=config["port"],
					host_port=config["port"],
					protocol=ecs.Protocol.TCP
				)] if container_index == 0 else None
			)
		tg_arn = get_ssm_parameter(self, f"/{prj_name}/{env_name}/targetgroup/{config["tg_name"]}")

		tg = elb.ApplicationTargetGroup.from_target_group_attributes(self, f"{tg_arn}TG", target_group_arn=tg_arn)
		main_container_name = containers[0]["name"]
		service = ecs.CfnService(
			self, f"{service_name}Service",
			cluster=cluster.cluster_name,
			service_name=f"{prj_name}-{env_name}-{service_name}-service",
			task_definition=task_def.task_definition_arn,
			launch_type="EC2",
			scheduling_strategy="DAEMON",
			load_balancers=[ecs.CfnService.LoadBalancerProperty(
				target_group_arn=tg.target_group_arn,
				container_name=main_container_name,
				container_port=config["port"]
			)]
		)
