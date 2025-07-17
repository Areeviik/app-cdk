from aws_cdk import (
	Stack,
	aws_ecr as ecr,
)
from constructs import Construct

class ECRStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, repo_names: list, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		prj_name = self.node.try_get_context("project_name")
		env_name = self.node.try_get_context("env")

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
