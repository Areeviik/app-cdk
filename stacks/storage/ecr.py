from aws_cdk import (
	Stack,
	aws_ecr as ecr,
)
from constructs import Construct
from utils.yaml_loader import load_yaml

class ECRStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, config_path: str, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		config = load_yaml(config_path)
		prj_name = config["project_name"]
		env_name = config["env"]

		repo_names = config.get("repositories", [])
		self.repositories = {}

		for name in repo_names:
			repo = ecr.Repository(
				self,
				f"{name}repo",
				repository_name=f"{prj_name}-{env_name}-{name}",
				lifecycle_rules=[
					ecr.LifecycleRule(max_image_count=10)
				],
				image_scan_on_push=True,
			)
			self.repositories[name] = repo

