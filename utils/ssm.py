from aws_cdk import Stack
from aws_cdk.aws_ssm import StringParameter
from constructs import Construct
from aws_cdk import aws_ssm as ssm

def get_ssm_parameter(scope: Stack, name: str) -> str:
    return StringParameter.value_for_string_parameter(scope, name)

def put_ssm_parameter(scope: Construct, name: str, value: str):
    StringParameter(
        scope,
        f"{name.replace('/', '_')}_param",
        parameter_name=name,
        string_value=value,
    )

def get_ssm_subnet_ids(scope: Construct, base_path: str, count: int) -> list[str]:
    return [
        ssm.StringParameter.value_for_string_parameter(
            scope, f"{base_path}/{i}"
        ) for i in range(count)
    ]
