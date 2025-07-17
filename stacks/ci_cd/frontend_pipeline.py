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

class CodePipelineFrontendStack(Stack):
    def __init__(
			self, scope: Construct, construct_id: str,
            artifactbucket: s3.Bucket,
            github_username: str,
            github_repo: str,
            github_branch: str,
            vpc: ec2.Vpc,
			**kwargs):
        super().__init__(scope, construct_id, **kwargs)

        prj_name = self.node.try_get_context("project_name")
        env_name = self.node.try_get_context("env")

        artifactbucket = s3.Bucket.from_bucket_name(
            self, "ArtifactBucket", artifactbucket.bucket_name
        )

        github_token_secret = sm.Secret.from_secret_name_v2(
            self, "GitHubToken",
            f"{prj_name}/{env_name}/github-token"
        )
        github_token = github_token_secret.secret_value_from_json("github-token")

        build_project= codebuild.PipelineProject(self, "FrontendBuildProject",
            project_name=f"{prj_name}-{env_name}-frontend-build",
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                privileged=True
            ),
            cache=codebuild.Cache.bucket(
                artifactbucket,
                prefix="frontend-codebuild-cache"),
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec.yml"),
        )

        pipeline = codepipeline.Pipeline(self, "FrontendPipeline",
            pipeline_name=f"{prj_name}-{env_name}-frontend-pipeline-v2",
            artifact_bucket=artifactbucket,
            restart_execution_on_update=False,
        )

        source_output = codepipeline.Artifact(artifact_name="source")
        build_output = codepipeline.Artifact(artifact_name="build")

        cluster = ecs.Cluster.from_cluster_attributes(self, "ImportedCluster",
            cluster_name=f"{prj_name}-{env_name}-ecs-cluster",
            vpc=vpc,
            security_groups=[],
        )

        service = ecs.Ec2Service.from_ec2_service_attributes(self, "ImportedService",
            service_name=f"{prj_name}-{env_name}-frontend-service",
            cluster=cluster,
        )

        pipeline.add_stage(
            stage_name="Source",
            actions=cast(list[IAction], [
                cpactions.GitHubSourceAction(
                    oauth_token=github_token,
                    output=source_output,
                    repo=github_repo,
                    branch=github_branch,
                    owner=github_username,
                    action_name="GitHub_Source",
                )
            ]),
        )
        pipeline.add_stage(
            stage_name="Build",
            actions=cast(list[IAction], [
                cpactions.CodeBuildAction(
                    action_name="Frontend_Build",
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
                    action_name="Frontend_Deploy",
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

