from aws_cdk import (
	Stack,
	aws_rds as rds,
	aws_ec2 as ec2,
	aws_secretsmanager as secretsmanager,
)
from constructs import Construct
from utils.ssm import get_ssm_parameter, put_ssm_parameter, get_ssm_subnet_ids
from utils.yaml_loader import load_yaml

class RDSStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, config_path, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		config = load_yaml(config_path)
		prj_name = config["project_name"]
		env_name = config["env"]

		for db in config["instances"]:
			name = db["name"]
			dbname = db["db_name"]
			uname = db["username"]
			engine_version = db.get("engine_version", "VER_17")
			vpc = db["vpc"]
			subnet_type = db["subnet_type"]
			azs = db.get("availability_zones", [])
			sg_name = db["security_group_name"]
			parameters = db.get("parameter_group", {})

			vpc_param = f"/{prj_name}/{env_name}/vpc/{vpc}"
			vpc_id = get_ssm_parameter(self, vpc_param)
			subnet_ids = get_ssm_subnet_ids(self, f"/{prj_name}/{env_name}/{vpc}/subnet/{subnet_type.lower()}", len(azs))
			sg_id = get_ssm_parameter(self, f"/{prj_name}/{env_name}/sg/{sg_name}")
			vpc = ec2.Vpc.from_vpc_attributes(
				self,
				f"{name}-vpc",
				vpc_id=vpc_id,
				availability_zones=azs,
				public_subnet_ids=subnet_ids if subnet_type == "PUBLIC" else None,
			)
			db_sg = ec2.SecurityGroup.from_security_group_id(self, f"{name}-sg", sg_id)

			db_secret = secretsmanager.Secret(
				self,
				f"{name}-secret",
				secret_name=f"{prj_name}/{env_name}/rds/{name}/credentials",
				generate_secret_string=secretsmanager.SecretStringGenerator(
					secret_string_template=f'{{"username":"{uname}"}}',
					generate_string_key="password",
					exclude_characters='/@"',
					include_space=False,
					password_length=12
				)
			)

			param_group = rds.ParameterGroup(
				self, f"{name}-params",
				engine=rds.DatabaseInstanceEngine.postgres(
					version=rds.PostgresEngineVersion.of(engine_version, engine_version)
				),
				parameters=parameters,
			)

			instance = rds.DatabaseInstance(
				self,
				f"{name}-rds",
				instance_identifier=name,
				engine=rds.DatabaseInstanceEngine.postgres(
					version=rds.PostgresEngineVersion.of(engine_version, engine_version)
				),
				credentials=rds.Credentials.from_secret(db_secret),
				instance_type=ec2.InstanceType.of(
					getattr(ec2.InstanceClass, db["instance_class"]),
					getattr(ec2.InstanceSize, db["instance_size"]),
				),
				vpc=vpc,
				vpc_subnets={"subnet_type": getattr(ec2.SubnetType, subnet_type)},
				security_groups=[db_sg],
				multi_az=db["multi_az"],
				storage_encrypted=db["storage_encrypted"],
				deletion_protection=db["deletion_protection"],
				parameter_group=param_group,
				database_name=dbname,
				port=db["port"]
			)

			put_ssm_parameter(self, f"/{prj_name}/{env_name}/rds/{name}/endpoint", instance.db_instance_endpoint_address)
			put_ssm_parameter(self, f"/{prj_name}/{env_name}/rds/{name}/secret", db_secret.secret_name)
