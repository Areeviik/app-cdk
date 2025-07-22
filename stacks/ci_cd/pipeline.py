from aws_cdk import (
	Stack,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as cpactions,
    aws_codebuild as codebuild,
    aws_s3 as s3,
    aws_secretsmanager as sm,
    aws_iam as iam,
    aws_ecs as ecs,
    aws_ec2 as ec2,
)
from constructs import Construct
from typing import cast, Dict, Any, List
from aws_cdk.aws_codepipeline import IAction
from aws_cdk.aws_codebuild import IProject
from utils.yaml_loader import load_yaml
from utils.ssm import get_ssm_parameter, get_ssm_subnet_ids

class CodePipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, config_path: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.config = load_yaml(config_path)
        self.prj_name = self.config["project_name"]
        self.env_name = self.config["env"]

        for svc_name, svc_conf in self.config.get("pipelines", {}).items():
            self._create_service_pipeline(svc_name, svc_conf)

    def _get_github_token(self, pipeline_name: str) -> str:
        """Retrieves the GitHub token from Secrets Manager."""
        github_token_secret = sm.Secret.from_secret_name_v2(
            self, f"{pipeline_name}GitHubToken", f"{self.prj_name}/{self.env_name}/github-token"
        )
        return github_token_secret.secret_value_from_json("github-token")

    def _create_service_pipeline(self, svc_name: str, svc_conf: Dict[str, Any]):
        """Creates a CodePipeline for a given service."""
        vpc = self._get_vpc(svc_name, svc_conf["vpc"], svc_conf.get("availability_zones", []))
        artifact_bucket = s3.Bucket.from_bucket_name(self, f"{svc_name.capitalize()}ArtifactBucket", svc_conf["artifact_bucket"])
        github_token = self._get_github_token(svc_name)
        github_username = svc_conf["github_username"]
        repo = svc_conf["repo"]
        branch = svc_conf["branch"]
        cache_prefix = svc_conf["build_cache_prefix"]
        ecs_service_name = svc_conf["ecs_service_name"]
        ecs_cluster_name = svc_conf["ecs_cluster_name"]

        build_project = self._create_codebuild_project(svc_name, cache_prefix,
                                                           svc_conf.get("codebuild_policy_statements", []), artifact_bucket)

        pipeline = codepipeline.Pipeline(
            self,
            f"{svc_name.capitalize()}Pipeline",
            pipeline_name=f"{self.prj_name}-{self.env_name}-{svc_name}-pipeline",
            artifact_bucket=artifact_bucket,
            restart_execution_on_update=False,
        )

        source_output = codepipeline.Artifact(artifact_name="source")
        build_output = codepipeline.Artifact(artifact_name="build")

        cluster = ecs.Cluster.from_cluster_attributes(
            self,
            f"{svc_name.capitalize()}Cluster",
            cluster_name=ecs_cluster_name,
            vpc=vpc,
            security_groups=[],
        )

        service = ecs.Ec2Service.from_ec2_service_attributes(
            self,
            f"{svc_name.capitalize()}Service",
            service_name=ecs_service_name,
            cluster=cluster,
        )

        for stage_conf in svc_conf.get("stages", []):
            stage_name = stage_conf["name"]
            actions_list: List[IAction] = []
            for action_conf in stage_conf.get("actions", []):
                action = self._create_action(
                    action_conf,
                    svc_name,
                    repo,
                    branch,
                    source_output,
                    build_output,
                    github_token,
                    build_project,
                    service,
                    github_username
                )
                if action:
                    actions_list.append(action)

            if actions_list:
                pipeline.add_stage(
                    stage_name=stage_name,
                    actions=cast(list[IAction], actions_list),
                )

    def _get_vpc(self, pipeline_name: str, vpc_name:str, azs:list) -> ec2.IVpc:
        """Retrieves the VPC based on config."""
        public_subnet_ids = get_ssm_subnet_ids(
            self, f"/{self.prj_name}/{self.env_name}/{vpc_name}/subnet/public", 2
        )
        vpc_param = f"/{self.prj_name}/{self.env_name}/vpc/{vpc_name}"
        vpc_id = get_ssm_parameter(self, vpc_param)
        return ec2.Vpc.from_vpc_attributes(
            self,
            f"{pipeline_name}Vpc",
            vpc_id=vpc_id,
            availability_zones=[az for az in azs],
            public_subnet_ids=public_subnet_ids,
        )

    def _create_codebuild_project(
            self, svc_name: str,
            cache_prefix: str,
            policy_statements_config: List[Dict[str, Any]],
            artifact_bucket: s3.IBucket
    ) -> codebuild.PipelineProject:
        """Creates a CodeBuild project and attaches policies."""
        build_project = codebuild.PipelineProject(
            self,
            f"{svc_name.capitalize()}BuildProject",
            project_name=f"{self.prj_name}-{self.env_name}-{svc_name}-build",
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0, privileged=True
            ),
            cache=codebuild.Cache.bucket(artifact_bucket, prefix=cache_prefix),
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec.yml"),
        )
        for policy_conf in policy_statements_config:
            build_project.add_to_role_policy(
                iam.PolicyStatement(
                    actions=policy_conf.get("actions", []),
                    resources=policy_conf.get("resources", ["*"]),
                    effect=getattr(iam.Effect, policy_conf.get("effect", "ALLOW").upper())
                )
            )
        return build_project

    def _create_action(
            self,
            action_conf: Dict[str, Any],
            svc_name: str,
            repo: str,
            branch: str,
            source_output: codepipeline.Artifact,
            build_output: codepipeline.Artifact,
            github_token: str,
            build_project: codebuild.PipelineProject,
            ecs_service: ecs.IEc2Service,
            github_username: str
        ) -> IAction | None:
        """Factory method to create different types of CodePipeline actions."""
        action_type = action_conf["type"]
        action_name = action_conf.get("action_name", f"{svc_name.capitalize()}_{action_type.capitalize()}")

        if action_type == "github_source":
            return cpactions.GitHubSourceAction(
                oauth_token=github_token,
                output=source_output,
                repo=repo,
                branch=branch,
                owner=github_username,
                action_name=action_name,
            )
        elif action_type == "codebuild":
            return cpactions.CodeBuildAction(
                action_name=action_name,
                project=cast(IProject, build_project),
                input=source_output,
                outputs=[build_output],
                variables_namespace=action_conf.get("variables_namespace")
            )
        elif action_type == "ecs_deploy":
            return cpactions.EcsDeployAction(
                action_name=action_name,
                service=ecs_service,
                input=build_output,
            )
        else:
            self.node.add_warning(f"Unsupported action type: {action_type}")
            return None
