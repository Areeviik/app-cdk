from aws_cdk import (
	Stack,
	aws_rds as rds,
	aws_ec2 as ec2,
	aws_secretsmanager as secretsmanager,
)
from constructs import Construct

class RDSStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, vpc: ec2.Vpc, db_sg: ec2.SecurityGroup, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		prj_name = self.node.try_get_context("project_name")
		env_name = self.node.try_get_context("env")

		db_secret = secretsmanager.Secret(
			self,
			"DBSecret",
			secret_name=f"{prj_name}/{env_name}/rds-credentials",
			generate_secret_string=secretsmanager.SecretStringGenerator(
				secret_string_template='{"username":"postgres"}',
				generate_string_key="password",
				exclude_characters='/@"',
				include_space=False,
				password_length=12
			)
		)

		param_group = rds.ParameterGroup(
			self, "PostgresParamGroup",
			engine=rds.DatabaseInstanceEngine.postgres(
				version=rds.PostgresEngineVersion.VER_17
			),
			parameters={
				"rds.force_ssl": "0",
			}
		)

		self.db_instance = rds.DatabaseInstance(
			self,
			"DatabaseInstance",
			instance_type=ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MICRO),
			vpc=vpc,
			vpc_subnets={
				"subnet_type": ec2.SubnetType.PRIVATE_ISOLATED
			},
			security_groups=[db_sg],
			parameter_group=param_group,
			engine=rds.DatabaseInstanceEngine.postgres(
				version=rds.PostgresEngineVersion.VER_17
			),
			multi_az=False,
			storage_encrypted=False,
			deletion_protection=False,
			database_name="vendure",
			credentials=rds.Credentials.from_secret(db_secret),
			port=5432
		)
