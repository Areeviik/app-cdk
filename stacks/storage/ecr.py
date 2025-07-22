from aws_cdk import (
	Stack,
	aws_ecr as ecr,
)
from constructs import Construct
from utils.yaml_loader import load_yaml
from utils.ssm import put_ssm_parameter

class ECRStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, config_path: str, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		config = load_yaml(config_path)
		prj_name = config["project_name"]
		env_name = config["env"]

		repo_names = config.get("repositories", [])
		self.repositories = {}

		for repo_cfg in repo_names:
			if isinstance(repo_cfg, str):
				repo_cfg = {"name": repo_cfg}

			name = repo_cfg["name"]
			repo_id = name.replace("-", "").capitalize() + "ECR"
			repo_name = repo_cfg.get("repository_name", f"{prj_name}-{env_name}-{name}")
			lifecycle_max = repo_cfg.get("lifecycle_max", 10)
			image_scan = repo_cfg.get("image_scan_on_push", True)
			store_ssm = repo_cfg.get("store_ssm", True)
			ssm_param_name = repo_cfg.get("ssm_param_name", f"/{prj_name}/{env_name}/ecr/{name}-repository-uri")

			repo = ecr.Repository(
				self,
				repo_id,
				repository_name=repo_name,
				lifecycle_rules=[
					ecr.LifecycleRule(max_image_count=lifecycle_max)
				],
				image_scan_on_push=image_scan,
			)
			self.repositories[name] = repo

			if store_ssm:
				put_ssm_parameter(self, ssm_param_name, repo.repository_uri)
