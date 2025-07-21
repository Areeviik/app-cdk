from aws_cdk import aws_s3 as s3

def get_block_public_access(enabled: bool) -> s3.BlockPublicAccess:
    if enabled:
        return s3.BlockPublicAccess(
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True
        )
    return s3.BlockPublicAccess(
        block_public_acls=False,
        block_public_policy=False,
        ignore_public_acls=False,
        restrict_public_buckets=False
    )