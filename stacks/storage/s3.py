from aws_cdk import (
    Stack,
    aws_s3 as s3,
    aws_ssm as ssm,
    RemovalPolicy,
)
from constructs import Construct
from utils.s3_public import get_block_public_access
from utils.yaml_loader import load_yaml

class S3BucketStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, config_path: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        config = load_yaml(config_path)

        prj_name = config["project_name"]
        env_name = config["env"]
        buckets = config.get("buckets", [])

        for bucket_cfg in buckets:
            bucket_id = bucket_cfg["name"].replace("-", "").capitalize() + "Bucket"

            full_bucket_name = f"{prj_name}-{env_name}-{bucket_cfg['name']}"
            access_control = getattr(s3.BucketAccessControl, bucket_cfg.get("access_control", "PRIVATE"))
            encryption = getattr(s3.BucketEncryption, bucket_cfg.get("encryption", "S3_MANAGED"))
            block_public = bucket_cfg.get("public_block", True)

            bucket = s3.Bucket(
                self,
                bucket_id,
                bucket_name=full_bucket_name,
                access_control=access_control,
                encryption=encryption,
                block_public_access=s3.BlockPublicAccess(
                    block_public_acls=bucket_cfg.get("block_public_acls", True),
                    block_public_policy= bucket_cfg.get("block_public_policy", True),
                    ignore_public_acls=bucket_cfg.get("ignore_public_acls", True),
                    restrict_public_buckets= bucket_cfg.get("restrict_public_buckets", True)
                ),
                removal_policy=RemovalPolicy.DESTROY,
                auto_delete_objects=True,
            )

            ssm.StringParameter(
                self,
                f"{bucket_id}Param",
                parameter_name=f"/{prj_name}/{env_name}/s3/{bucket_cfg['name']}-bucket-name",
                string_value=bucket.bucket_name,
            )
