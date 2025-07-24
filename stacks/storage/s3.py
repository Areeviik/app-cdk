from aws_cdk import (
    Stack,
    aws_s3 as s3,
    RemovalPolicy,
    Tags,
    Duration
)
from constructs import Construct
from typing import Dict, Any, List
from utils.yaml_loader import load_yaml
from utils.s3_public import get_block_public_access
from utils.ssm import put_ssm_parameter

class S3BucketStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, config_path: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.config = load_yaml(config_path)
        self.prj_name = self.config["project_name"]
        self.env_name = self.config["env"]

        self.buckets: Dict[str, s3.Bucket] = {}

        self._create_buckets()

    def _create_buckets(self):
        bucket_configs = self.config.get("buckets", [])

        if not bucket_configs:
            print(f"WARNING: No S3 bucket configurations found in the YAML file for {self.stack_name}.")
            return

        for bucket_cfg in bucket_configs:
            self._create_base_bucket(bucket_cfg)

        for bucket_cfg in bucket_configs:
            self._configure_bucket_logging(bucket_cfg)

    def _create_base_bucket(self, bucket_cfg: Dict[str, Any]):
        bucket_name_in_config = bucket_cfg["name"]
        full_bucket_name = f"{self.prj_name}-{self.env_name}-{bucket_name_in_config}"
        bucket_logical_id = f"{bucket_name_in_config.replace('-', '')}Bucket"

        access_control_str = bucket_cfg.get("access_control", "PRIVATE")
        access_control = getattr(s3.BucketAccessControl, access_control_str.upper())

        encryption_str = bucket_cfg.get("encryption", "S3_MANAGED")
        encryption = getattr(s3.BucketEncryption, encryption_str.upper())

        public_access_conf = bucket_cfg.get("public_access_block", {})
        block_public_access = get_block_public_access(public_access_conf)

        removal_policy_str = bucket_cfg.get("removal_policy", "RETAIN")
        removal_policy = getattr(RemovalPolicy, removal_policy_str.upper())
        auto_delete_objects = bucket_cfg.get("auto_delete_objects", False)

        versioned = bucket_cfg.get("versioned", False)

        bucket = s3.Bucket(
            self,
            bucket_logical_id,
            bucket_name=full_bucket_name,
            access_control=access_control,
            encryption=encryption,
            block_public_access=block_public_access,
            removal_policy=removal_policy,
            auto_delete_objects=auto_delete_objects,
            versioned=versioned,
        )

        self.buckets[bucket_name_in_config] = bucket

        self._add_lifecycle_rules(bucket, bucket_cfg.get("lifecycle_rules", []))

        Tags.of(bucket).add("Project", self.prj_name)
        Tags.of(bucket).add("Environment", self.env_name)
        Tags.of(bucket).add("Name", full_bucket_name)
        Tags.of(bucket).add("BucketNameInConfig", bucket_name_in_config)

        put_ssm_parameter(
            self,
            f"/{self.prj_name}/{self.env_name}/s3/{bucket_name_in_config}/name",
            bucket.bucket_name,
        )

    def _configure_bucket_logging(self, bucket_cfg: Dict[str, Any]):
        bucket_name_in_config = bucket_cfg["name"]
        bucket = self.buckets[bucket_name_in_config]
        logging_target_bucket_name = bucket_cfg.get("logging_target_bucket")
        logging_prefix = bucket_cfg.get("logging_prefix")

        if logging_target_bucket_name:
            target_bucket = self.buckets.get(logging_target_bucket_name)

            if not target_bucket:
                print(
                    f"INFO: Logging target bucket '{logging_target_bucket_name}' not found in this stack's config. Attempting to import by name.")
                try:
                    target_bucket = s3.Bucket.from_bucket_name(
                        self,
                        f"{logging_target_bucket_name.replace('-', '')}LoggingBucketImport",
                        logging_target_bucket_name
                    )
                except Exception as e:
                    print(f"ERROR: Failed to import logging target bucket '{logging_target_bucket_name}': {e}")
                    return

            bucket.set_logging_target(
                target_bucket=target_bucket,
                target_prefix=logging_prefix
            )
            print(
                f"INFO: Configured logging for bucket '{bucket.bucket_name}' to '{target_bucket.bucket_name}' with prefix '{logging_prefix or ''}'.")

    def _add_lifecycle_rules(self, bucket: s3.Bucket, rules_config: List[Dict[str, Any]]):
        if not rules_config:
            return

        for i, rule_cfg in enumerate(rules_config):
            transitions: List[s3.Transition] = []
            for trans_cfg in rule_cfg.get("transitions", []):
                transitions.append(s3.Transition(
                    storage_class=getattr(s3.StorageClass, trans_cfg["storage_class"].upper()),
                    transition_after=Duration.days(trans_cfg["transition_after_days"])
                ))

            noncurrent_version_transitions: List[s3.NoncurrentVersionTransition] = []
            for nc_trans_cfg in rule_cfg.get("noncurrent_version_transitions", []):
                noncurrent_version_transitions.append(s3.NoncurrentVersionTransition(
                    storage_class=getattr(s3.StorageClass, nc_trans_cfg["storage_class"].upper()),
                    transition_after=Duration.days(nc_trans_cfg["transition_after_days"])
                ))

            abort_incomplete_multipart_upload_after = rule_cfg.get("abort_incomplete_multipart_upload_after_days")
            if abort_incomplete_multipart_upload_after is not None:
                abort_incomplete_multipart_upload_after = Duration.days(abort_incomplete_multipart_upload_after)

            bucket.add_lifecycle_rule(
                id=f"LifecycleRule{i + 1}-{rule_cfg.get('id', 'default')}",
                enabled=rule_cfg.get("enabled", True),
                expiration=Duration.days(rule_cfg["expiration_days"]) if rule_cfg.get("expiration_days") else None,
                noncurrent_version_expiration=Duration.days(
                    rule_cfg["noncurrent_version_expiration_days"]) if rule_cfg.get(
                    "noncurrent_version_expiration_days") else None,
                noncurrent_versions_to_retain=rule_cfg.get("noncurrent_versions_to_retain"),
                prefix=rule_cfg.get("prefix"),
                tag_filters=rule_cfg.get("tag_filters"),
                transitions=transitions,
                noncurrent_version_transitions=noncurrent_version_transitions,
                abort_incomplete_multipart_upload_after=abort_incomplete_multipart_upload_after,
            )
