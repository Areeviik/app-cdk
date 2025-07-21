from aws_cdk import (
	Stack,
	aws_rds as rds,
	aws_ec2 as ec2,
	aws_secretsmanager as secretsmanager,
	aws_ssm as ssm
)
from constructs import Construct
from utils.ssm import get_ssm_parameter
from utils.ssm import put_ssm_parameter
from utils.yaml_loader import load_yaml

def get_ssm_subnet_ids(scope: Construct, base_path: str, count: int) -> list[str]:
	return [
		ssm.StringParameter.value_for_string_parameter(
			scope, f"{base_path}/{i}"
		) for i in range(count)
	]

class RDSStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, config_path, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		config = load_yaml(config_path)
		prj_name = config["project_name"]
		env_name = config["env"]
		public_subnet_ids = get_ssm_subnet_ids(
			self, f"/{prj_name}/{env_name}/subnet/public", 2
		)

		vpc_param = f"/{prj_name}/{env_name}/vpc/{config['vpc']}"
		vpc_id = get_ssm_parameter(self, vpc_param)
		vpc = ec2.Vpc.from_vpc_attributes(
			self,
			"Vpc",
			vpc_id=vpc_id,
			availability_zones=[az for az in config.get("availability_zones")],
			public_subnet_ids=public_subnet_ids,
		)

		db_sg_param = f"/{prj_name}/{env_name}/sg/db-sg"
		db_sg_id = get_ssm_parameter(self, db_sg_param)
		db_sg = ec2.SecurityGroup.from_security_group_id(self, "DBSG", db_sg_id)

		for db in config["instances"]:
			name = db["name"]
			dbname = db["db_name"]
			uname = db["username"]

			db_secret = secretsmanager.Secret(
				self,
				f"{name}-secret",
				secret_name=f"{prj_name}/{env_name}/rds-credentials",
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
					version=rds.PostgresEngineVersion.VER_17
				),
				parameters={"rds.force_ssl": "0"}
			)

			instance = rds.DatabaseInstance(
				self,
				f"{name}-rds",
				instance_identifier=name,
				engine=rds.DatabaseInstanceEngine.postgres(
					version=rds.PostgresEngineVersion.VER_17
				),
				credentials=rds.Credentials.from_secret(db_secret),
				instance_type=ec2.InstanceType.of(
					getattr(ec2.InstanceClass, db["instance_class"]),
					getattr(ec2.InstanceSize, db["instance_size"]),
				),
				vpc=vpc,
				vpc_subnets={"subnet_type": getattr(ec2.SubnetType, db["subnet_type"])},
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
