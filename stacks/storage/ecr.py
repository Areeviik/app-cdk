from aws_cdk import (
	Stack,
	aws_ecr as ecr,
	aws_iam as iam,
	RemovalPolicy,
	Tags,
	Duration
)
from constructs import Construct
from typing import Dict, Any, List, Optional
from utils.yaml_loader import load_yaml
from utils.ssm import put_ssm_parameter

class ECRStack(Stack):
	def __init__(self, scope: Construct, construct_id: str, config_path: str, **kwargs) -> None:
		super().__init__(scope, construct_id, **kwargs)

		self.config = load_yaml(config_path)
		self.prj_name = self.config["project_name"]
		self.env_name = self.config["env"]

		self.repositories: Dict[str, ecr.Repository] = {}

		self._create_repositories()

	def _create_repositories(self):
		repo_configs = self.config.get("repositories", [])

		if not repo_configs:
			print(f"WARNING: No ECR repository configurations found in the YAML file for {self.stack_name}.")
			return

		for repo_cfg in repo_configs:
			self._create_single_repository(repo_cfg)

	def _create_single_repository(self, repo_cfg: Dict[str, Any]):
		name_in_config = repo_cfg["name"]
		repository_name = repo_cfg.get("repository_name", f"{self.prj_name}-{self.env_name}-{name_in_config}")

		image_scan_on_push = repo_cfg.get("image_scan_on_push", True)
		image_tag_mutability_str = repo_cfg.get("image_tag_mutability", "MUTABLE")
		image_tag_mutability = getattr(ecr.TagMutability, image_tag_mutability_str.upper())
		encryption_str = repo_cfg.get("encryption", "AES_256")
		encryption = getattr(ecr.RepositoryEncryption, encryption_str.upper())
		kms_key = repo_cfg.get("kms_key_arn")

		removal_policy_str = repo_cfg.get("removal_policy", "RETAIN")
		removal_policy = getattr(RemovalPolicy, removal_policy_str.upper())
		auto_delete_images = repo_cfg.get("auto_delete_images", False)

		repo_logical_id = f"{name_in_config.replace('-', '')}Repository"

		lifecycle_rules_configs = repo_cfg.get("lifecycle_rules", [])
		lifecycle_rules = self._resolve_lifecycle_rules(lifecycle_rules_configs)

		repo_props = {
			"repository_name": repository_name,
			"image_scan_on_push": image_scan_on_push,
			"image_tag_mutability": image_tag_mutability,
			"lifecycle_rules": lifecycle_rules,
			"removal_policy": removal_policy,
			"empty_on_delete": auto_delete_images,
		}

		if encryption == ecr.RepositoryEncryption.KMS:
			if not kms_key:
				raise ValueError(
					f"ECR Repository '{name_in_config}' configured with KMS encryption but no 'kms_key_arn' provided.")
			repo_props["encryption"] = encryption

			repo_props["encryption_key"] = None

		else:
			repo_props["encryption"] = encryption
		repo = ecr.Repository(self, repo_logical_id, **repo_props)
		self.repositories[name_in_config] = repo

		repo_policy_configs = repo_cfg.get("repository_policy_statements", [])
		if repo_policy_configs:
			iam_statements = self._resolve_repository_policy(repo_policy_configs)
			for statement in iam_statements:
				repo.add_to_resource_policy(statement)

			print(f"INFO: Configured repository policy for ECR repository '{repository_name}'.")

		Tags.of(repo).add("Project", self.prj_name)
		Tags.of(repo).add("Environment", self.env_name)
		Tags.of(repo).add("Name", repository_name)
		Tags.of(repo).add("RepoNameInConfig", name_in_config)

		store_ssm = repo_cfg.get("store_ssm", True)
		if store_ssm:
			ssm_uri_param_name = repo_cfg.get("ssm_uri_param_name",f"/{self.prj_name}/{self.env_name}/ecr/{name_in_config}/uri")
			put_ssm_parameter(self, ssm_uri_param_name, repo.repository_uri)

			ssm_arn_param_name = repo_cfg.get("ssm_arn_param_name",f"/{self.prj_name}/{self.env_name}/ecr/{name_in_config}/arn")
			put_ssm_parameter(self, ssm_arn_param_name, repo.repository_arn)

	def _resolve_lifecycle_rules(self, rules_configs: List[Dict[str, Any]]) -> List[ecr.LifecycleRule]:
		ecr_rules: List[ecr.LifecycleRule] = []
		for rule_cfg in rules_configs:
			rule_id = rule_cfg.get("id", f"Rule{rule_cfg.get('rule_priority', '')}")

			max_image_count = rule_cfg.get("max_image_count")

			max_image_age: Optional[Duration] = None
			if rule_cfg.get("max_image_age_days") is not None:
				max_image_age = Duration.days(rule_cfg["max_image_age_days"])

			tag_status_str = rule_cfg.get("tag_status", "ANY")
			tag_status = getattr(ecr.TagStatus, tag_status_str.upper())

			ecr_rule = ecr.LifecycleRule(
				rule_priority=rule_cfg.get("rule_priority"),
				description=rule_cfg.get("description"),
				max_image_count=max_image_count,
				max_image_age=max_image_age,
				tag_status=tag_status,
				tag_prefix_list=rule_cfg.get("tag_prefix_list"),
			)
			ecr_rules.append(ecr_rule)
		return ecr_rules

	def _resolve_repository_policy(self, policy_statements_config: List[
		Dict[str, Any]]) -> Any:

		statements: List[iam.PolicyStatement] = []
		for stmt_conf in policy_statements_config:
			actions = stmt_conf.get("actions", [])
			resources = stmt_conf.get("resources", ["*"])
			effect_str = stmt_conf.get("effect", "ALLOW").upper()
			effect = getattr(iam.Effect, effect_str)
			principals = self._resolve_policy_principals(stmt_conf.get("principals", []))

			statements.append(iam.PolicyStatement(
				actions=actions,
				resources=resources,
				effect=effect,
				principals=principals,
				conditions=stmt_conf.get("conditions")
			))
		return statements

	def _resolve_policy_principals(self, principals_config: List[Dict[str, Any]]) -> List[iam.IPrincipal]:
		from aws_cdk import aws_iam as iam
		principals: List[iam.IPrincipal] = []
		for principal_conf in principals_config:
			principal_type = principal_conf["type"]
			if principal_type == "arn":
				principals.append(iam.ArnPrincipal(principal_conf["arn"]))
			elif principal_type == "account_root":
				principals.append(iam.AccountRootPrincipal())
			elif principal_type == "service":
				principals.append(iam.ServicePrincipal(principal_conf["service"]))
			elif principal_type == "federated":
				principals.append(iam.FederatedPrincipal(
					federated=principal_conf["federated"],
					conditions=principal_conf.get("conditions")
				))
			elif principal_type == "any_aws":
				principals.append(iam.AnyPrincipal())
			else:
				raise ValueError(f"Unsupported principal type: {principal_type}")
		return principals