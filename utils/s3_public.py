from aws_cdk import aws_s3 as s3
from typing import Dict, Any

def get_block_public_access(config: Dict[str, Any]) -> s3.BlockPublicAccess:

    return s3.BlockPublicAccess(
        block_public_acls=config.get("block_public_acls", True),
        block_public_policy=config.get("block_public_policy", True),
        ignore_public_acls=config.get("ignore_public_acls", True),
        restrict_public_buckets=config.get("restrict_public_buckets", True)
    )
