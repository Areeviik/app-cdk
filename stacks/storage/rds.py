from aws_cdk import (
	Stack,
	aws_rds as rds,
	aws_ec2 as ec2,
	aws_secretsmanager as secretsmanager,
	Tags,
	Duration
)
from constructs import Construct
from typing import Dict, Any, List, Optional
from utils.ssm import get_ssm_parameter, put_ssm_parameter, get_ssm_subnet_ids
from utils.yaml_loader import load_yaml

class RDSStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, config_path, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		self.config = load_yaml(config_path)
		self.prj_name = self.config["project_name"]
		self.env_name = self.config["env"]

		self.vpcs: Dict[str, ec2.IVpc] = {}
		self.security_groups: Dict[str, ec2.ISecurityGroup] = {}
		self.db_secrets: Dict[str, secretsmanager.ISecret] = {}

		self._create_database_instances()

	def _create_database_instances(self):
		db_configs = self.config.get("instances", [])
		if not db_configs:
			print(f"WARNING: No database instance configurations found in the YAML file for {self.stack_name}.")
			return

		for db_conf in db_configs:
			self._create_single_db_instance(db_conf)

	def _create_single_db_instance(self, db_conf: Dict[str, Any]):
		name = db_conf["name"]
		db_name = db_conf["db_name"]
		username = db_conf["username"]
		engine_conf = db_conf["engine"]
		instance_class = getattr(ec2.InstanceClass, db_conf["instance_class"].upper())
		instance_size = getattr(ec2.InstanceSize, db_conf["instance_size"].upper())
		multi_az = db_conf.get("multi_az", False)
		storage_encrypted = db_conf.get("storage_encrypted", True)
		deletion_protection = db_conf.get("deletion_protection", True)
		subnet_type = db_conf.get("subnet_type", "PRIVATE_WITH_EGRESS").upper()
		port = db_conf.get("port", 5432)
		vpc_key = db_conf["vpc"]
		sg_name = db_conf["security_group_name"]
		parameters = db_conf.get("parameter_group_params", {})
		allocated_storage_gb = db_conf.get("allocated_storage_gb", 100)
		max_allocated_storage_gb = db_conf.get("max_allocated_storage_gb")
		backup_retention_days = db_conf.get("backup_retention_days", 7)
		preferred_backup_window = db_conf.get("preferred_backup_window")
		preferred_maintenance_window = db_conf.get("preferred_maintenance_window")
		publicly_accessible = db_conf.get("publicly_accessible", False)
		option_group_conf = db_conf.get("option_group", {})
		read_replica_of = db_conf.get("read_replica_of")

		azs = db_conf.get("availability_zones", [])
		if not azs and multi_az:
			print(f"INFO: Multi-AZ enabled for {name}, but no AZs specified. Defaulting to 2 AZs for VPC import.")
			azs = ["_"] * 2

		vpc = self._get_vpc(vpc_key, azs, subnet_type)
		db_sg = self._get_security_group(sg_name)

		vpc_subnets_selection: Dict[str, Any] = {"subnet_type": getattr(ec2.SubnetType, subnet_type)}

		db_secret_logical_id = f"{name}-secret"
		db_secret = secretsmanager.Secret(
			self,
			db_secret_logical_id,
			secret_name=f"{self.prj_name}/{self.env_name}/rds/{name}/credentials",
			generate_secret_string=secretsmanager.SecretStringGenerator(
				secret_string_template=f'{{"username":"{username}"}}',
				generate_string_key="password",
				exclude_characters='/@"',
				include_space=False,
				password_length=db_conf.get("password_length", 16)
			)
		)
		self.db_secrets[name] = db_secret

		param_group_logical_id = f"{name}-params"
		param_group = rds.ParameterGroup(
			self, param_group_logical_id,
			engine=self._get_db_engine_version(engine_conf),
			parameters=parameters,
		)

		instance_logical_id = f"{name}-rds"

		if read_replica_of:
			if read_replica_of not in self.db_secrets:
				raise ValueError(f"Primary DB instance '{read_replica_of}' not found in configuration for read replica '{name}'. Ensure primary is defined before replica.")
			primary_instance_arn = get_ssm_parameter(self,f"/{self.prj_name}/{self.env_name}/rds/{read_replica_of}/arn")


			instance = rds.DatabaseInstanceReadReplica(
				self,
				instance_logical_id,
				source_database_instance_arn=primary_instance_arn,
				instance_identifier=name,
				instance_type=ec2.InstanceType.of(instance_class, instance_size),
				vpc=vpc,
				vpc_subnets=vpc_subnets_selection,
				security_groups=[db_sg],
				publicly_accessible=publicly_accessible,
			)

		else:
			instance = rds.DatabaseInstance(
				self,
				instance_logical_id,
				instance_identifier=name,
				engine=self._get_db_engine_version(engine_conf),
				credentials=rds.Credentials.from_secret(db_secret),
				instance_type=ec2.InstanceType.of(instance_class, instance_size),
				vpc=vpc,
				vpc_subnets=vpc_subnets_selection,
				security_groups=[db_sg],
				multi_az=multi_az,
				storage_encrypted=storage_encrypted,
				deletion_protection=deletion_protection,
				parameter_group=param_group,
				database_name=db_name,
				port=port,
				allocated_storage=allocated_storage_gb,
				max_allocated_storage=max_allocated_storage_gb,
				backup_retention=Duration.days(backup_retention_days),
				preferred_backup_window=preferred_backup_window,
				preferred_maintenance_window=preferred_maintenance_window,
				publicly_accessible=publicly_accessible,
			)

		Tags.of(instance).add("Project", self.prj_name)
		Tags.of(instance).add("Environment", self.env_name)
		Tags.of(instance).add("Name", f"{self.prj_name}-{self.env_name}-{name}-db")
		Tags.of(instance).add("DbNameInConfig", name)

		put_ssm_parameter(self, f"/{self.prj_name}/{self.env_name}/rds/{name}/arn", instance.instance_arn)
		put_ssm_parameter(self, f"/{self.prj_name}/{self.env_name}/rds/{name}/endpoint", instance.db_instance_endpoint_address)
		put_ssm_parameter(self, f"/{self.prj_name}/{self.env_name}/rds/{name}/endpoint/port",str(instance.db_instance_endpoint_port))
		put_ssm_parameter(self, f"/{self.prj_name}/{self.env_name}/rds/{name}/secret",db_secret.secret_name)

	def _get_vpc(self, vpc_key: str, azs: List[str], subnet_type: str) -> ec2.IVpc:
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
					self, f"{vpc_key}VpcImport", **vpc_attributes
				)
			except Exception as e:
				raise ValueError(f"Failed to import VPC '{vpc_key}' (SSM: {vpc_id_param_name}, Subnets: {ssm_subnet_base_path}): {e}")
		return self.vpcs[vpc_key]

	def _get_security_group(self, sg_name: str) -> ec2.ISecurityGroup:
		if sg_name not in self.security_groups:
			sg_id_param_name = f"/{self.prj_name}/{self.env_name}/sg/{sg_name}"
			try:
				sg_id = get_ssm_parameter(self, sg_id_param_name)
				self.security_groups[sg_name] = ec2.SecurityGroup.from_security_group_id(
					self, f"{sg_name}SGImport", sg_id
				)
			except Exception as e:
				raise ValueError(f"Failed to import Security Group '{sg_name}' (ID from SSM: {sg_id_param_name}): {e}")
		return self.security_groups[sg_name]

	def _get_db_engine_version(self, engine_conf: Dict[str, Any]) -> rds.IInstanceEngine:
		engine_type = engine_conf["type"].lower()
		engine_version_str = str(engine_conf["version"]).upper()
		if engine_type == "postgres":
			try:
				engine_version_enum = getattr(rds.PostgresEngineVersion, engine_version_str)
				return rds.DatabaseInstanceEngine.postgres(version=engine_version_enum)
			except AttributeError:
				raise ValueError(f"Unsupported Postgres Engine Version: '{engine_version_str}'. Check rds.PostgresEngineVersion for valid options (e.g., VER_14, VER_15, VER_16).")
		elif engine_type == "mysql":
			try:
				engine_version_enum = getattr(rds.MysqlEngineVersion, engine_version_str)
				return rds.DatabaseInstanceEngine.mysql(version=engine_version_enum)
			except AttributeError:
				raise ValueError(f"Unsupported MySQL Engine Version: '{engine_version_str}'. Check rds.MysqlEngineVersion for valid options.")
		else:
			raise ValueError(f"Unsupported database engine type: {engine_type}")

