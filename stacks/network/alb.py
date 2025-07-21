from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elb,
	aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_route53_targets as targets,
)
from constructs import Construct
from utils.yaml_loader import load_yaml
from utils.ssm import get_ssm_parameter, put_ssm_parameter, get_ssm_subnet_ids

class ALBStack(Stack):
	def __init__(
			self,
			scope: Construct,
			construct_id: str,
			config_path: str,
			**kwargs
	) -> None:
		super().__init__(scope, construct_id, **kwargs)

		config = load_yaml(config_path)
		prj_name = config["project_name"]
		env_name = config["env"]
		self.vpcs = {}

		for alb_conf in config.get("albs", []):
			vpc_key = alb_conf["vpc"]
			if vpc_key not in self.vpcs:
				vpc_param_name = f"/{prj_name}/{env_name}/vpc/{vpc_key}"
				vpc_id = get_ssm_parameter(self, vpc_param_name)
				self.vpcs[vpc_key] = ec2.Vpc.from_vpc_attributes(
					self, f"VPC-{vpc_key}",
					vpc_id=vpc_id,
					availability_zones=[az for az in alb_conf.get("availability_zones")],
					public_subnet_ids=get_ssm_subnet_ids(
						self,
						f"/{prj_name}/{env_name}/vpc/{vpc_key}/public_subnets",
						len(alb_conf.get("availability_zones", []))
					),
				)
			vpc = self.vpcs[vpc_key]
			alb_name = alb_conf["name"]
			sg_id = get_ssm_parameter(self, f"/{prj_name}/{env_name}/sg/{alb_conf['sg']}")
			alb_sg = ec2.SecurityGroup.from_security_group_id(self, f"{alb_name}-SG", sg_id)

			domain_name = alb_conf["domain_name"]
			hosted_zone = route53.HostedZone.from_lookup(self, f"{alb_name}-HostedZone", domain_name=domain_name)
			cert = acm.Certificate(
				self,
				f"{alb_name}-Cert",
				certificate_name=f"{prj_name}-{env_name}-{alb_name}-cert",
				domain_name=alb_conf["subdomains"][0]["name"] + "." + domain_name,
				subject_alternative_names=[f"{s['name']}.{domain_name}" for s in alb_conf["subdomains"]],
				validation=acm.CertificateValidation.from_dns(hosted_zone),
			)

			alb = elb.ApplicationLoadBalancer(
				self,
				f"{alb_name}-ALB",
				load_balancer_name=f"{prj_name}-{env_name}-{alb_name}",
				vpc=vpc,
				internet_facing=True,
				security_group=alb_sg,
			)

			put_ssm_parameter(self, f"/{prj_name}/{env_name}/loadbalancer/{alb_name}", alb.load_balancer_arn)

			http_listener = alb.add_listener(
				f"{alb_name}-HTTPListener",
				port=80,
				open=True
			)
			http_listener.add_action(
				f"{alb_name}-HTTPRedirect",
				action=elb.ListenerAction.redirect(
					protocol="HTTPS",
					port="443"
				)
			)

			https_listener = alb.add_listener(
				f"{alb_name}-HTTPSListener",
				port=443,
				open=True,
				certificates=[cert],
				default_action = elb.ListenerAction.fixed_response(
					status_code=404,
					message_body="Not found"
				)
			)

			for i, subdomain_conf in enumerate(alb_conf["subdomains"]):
				fqdn = f"{subdomain_conf['name']}.{domain_name}"
				path = subdomain_conf.get("path", "/")
				port = subdomain_conf["port"]

				tg = elb.ApplicationTargetGroup(
					self,
					f"{alb_name}-TG-{i}",
					target_group_name=f"{alb_name}-{subdomain_conf['name']}tg",
					vpc=vpc,
					port=port,
					protocol=elb.ApplicationProtocol.HTTP,
					target_type=elb.TargetType.INSTANCE,
					health_check=elb.HealthCheck(path=path, healthy_http_codes="200")
				)

				put_ssm_parameter(
					self,
					f"/{prj_name}/{env_name}/targetgroup/{alb_name}-{subdomain_conf['name']}",
					tg.target_group_arn
				)

				elb.ApplicationListenerRule(
				self,
				f"{alb_name}-Rule-{i}",
				listener=https_listener,
				priority=10 + i,
				conditions=[elb.ListenerCondition.host_headers([fqdn])],
				action=elb.ListenerAction.forward([tg])
				)

				route53.ARecord(
					self,
					f"{fqdn.replace('.', '-')}-AliasRecord",
					zone=hosted_zone,
					record_name=fqdn,
					target=route53.RecordTarget.from_alias(targets.LoadBalancerTarget(alb))
				)
