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
from typing import cast
from aws_cdk.aws_codepipeline import IAction
from aws_cdk.aws_codebuild import IProject
from utils.yaml_loader import load_yaml
from utils.ssm import get_ssm_parameter, get_ssm_subnet_ids

class CodePipelineStack(Stack):
    def __init__(
			self, scope: Construct, construct_id: str, config_path: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        config = load_yaml(config_path)
        prj_name = config["project_name"]
        env_name = config["env"]

        artifactbucket = s3.Bucket.from_bucket_name(
            self, "ArtifactBucket", config["artifact_bucket"]
        )

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

        github_token_secret = sm.Secret.from_secret_name_v2(
            self, "GitHubToken",
            f"{prj_name}/{env_name}/github-token"
        )
        github_token = github_token_secret.secret_value_from_json("github-token")
        username = config["github_username"]

        for svc_name, svc_conf in config.get("pipelines", {}).items():
            repo = svc_conf["repo"]
            branch = svc_conf["branch"]
            cache_prefix = svc_conf["build_cache_prefix"]
            ecs_service_name = svc_conf["ecs_service_name"]

            build_project= codebuild.PipelineProject(self, f"{svc_name.capitalize()}BuildProject",
                project_name=f"{prj_name}-{env_name}-{svc_name}-build",
                environment=codebuild.BuildEnvironment(
                    build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                    privileged=True
                ),
                cache=codebuild.Cache.bucket(
                    artifactbucket,
                    prefix=cache_prefix
                ),
                build_spec=codebuild.BuildSpec.from_source_filename("buildspec.yml"),
            )

            pipeline = codepipeline.Pipeline(self, f"{svc_name.capitalize()}Pipeline",
                pipeline_name=f"{prj_name}-{env_name}-{svc_name}-pipeline",
                artifact_bucket=artifactbucket,
                restart_execution_on_update=False,
            )

            source_output = codepipeline.Artifact(artifact_name="source")
            build_output = codepipeline.Artifact(artifact_name="build")

            cluster = ecs.Cluster.from_cluster_attributes(
                self, f"{svc_name.capitalize()}Cluster",
                cluster_name=f"{prj_name}-{env_name}-ecs-cluster",
                vpc=vpc,
                security_groups=[],
            )

            service = ecs.Ec2Service.from_ec2_service_attributes(
                self, f"{svc_name.capitalize()}Service",
                service_name=ecs_service_name,
                cluster=cluster,
            )

            pipeline.add_stage(
                stage_name="Source",
                actions=cast(list[IAction], [
                    cpactions.GitHubSourceAction(
                        oauth_token=github_token,
                        output=source_output,
                        repo=repo,
                        branch=branch,
                        owner=username,
                        action_name="GitHub_Source",
                    )
                ]),
            )
            pipeline.add_stage(
                stage_name="Build",
                actions=cast(list[IAction], [
                    cpactions.CodeBuildAction(
                        action_name=f"{svc_name.capitalize()}_Build",
                        project=cast(IProject, build_project),
                        input=source_output,
                        outputs=[build_output],
                    )
                ])
            )

            pipeline.add_stage(
                stage_name="Deploy",
                actions=cast(list[IAction], [
                    cpactions.EcsDeployAction(
                        action_name=f"{svc_name.capitalize()}_Deploy",
                        service=service,
                        input=build_output,
                    )
                ])
            )

            build_project.add_to_role_policy(iam.PolicyStatement(
                actions=[
                    "ecs:DescribeServices",
                    "ecs:UpdateService",
                    "ecs:RegisterTaskDefinition",
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:CompleteLayerUpload",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:PutImage",
                    "ecr:UploadLayerPart",
                    "secretsmanager:GetSecretValue",
                    "s3:GetBucketAcl",
                    "s3:GetBucketLocation",
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                resources=["*"],
            ))

