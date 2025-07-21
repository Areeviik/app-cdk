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

		alb_sg_param = f"/{prj_name}/{env_name}/sg/alb-sg"
		alb_sg_id = get_ssm_parameter(self, alb_sg_param)
		alb_sg = ec2.SecurityGroup.from_security_group_id(self, "AlbSecurityGroup", alb_sg_id)

		domain_name = config["domain_name"]
		frontend_subdomain = config["frontend_subdomain"]
		backend_subdomain = config["backend_subdomain"]

		hosted_zone = route53.HostedZone.from_lookup(
			self,
			"HostedZone",
			domain_name=domain_name
		)

		frontend_domain = f"{frontend_subdomain}.{domain_name}"
		backend_domain = f"{backend_subdomain}.{domain_name}"

		certificate = acm.Certificate(
			self,
			"Certificate",
			certificate_name=f"{prj_name}-{env_name}-alb-cert",
			domain_name=frontend_domain,
			subject_alternative_names=[backend_domain],
			validation=acm.CertificateValidation.from_dns(hosted_zone),
		)

		self.alb = elb.ApplicationLoadBalancer(
			self,
			"ALB",
			load_balancer_name=f"{prj_name}-{env_name}-alb",
			vpc=vpc,
			internet_facing=True,
			security_group=alb_sg,
		)

		put_ssm_parameter(self, f"/{prj_name}/{env_name}/loadbalancer", self.alb.load_balancer_arn)


		http_listener = self.alb.add_listener(
			"HTTPListener",
			port=80,
			open=True
		)
		http_listener.add_action(
			"HttpRedirect",
			action=elb.ListenerAction.redirect(
				protocol="HTTPS",
				port="443"
			)
		)

		https_listener = self.alb.add_listener(
			"HTTPSListener",
			port=443,
			open=True,
			certificates=[certificate],
			default_action = elb.ListenerAction.fixed_response(
				status_code=404,
				content_type="text/plain",
				message_body="Not found"
			)
		)

		self.frontend_tg = elb.ApplicationTargetGroup(
			self, "FrontendTargetGroup",
			target_group_name=f"{prj_name}-{env_name}-frontend-tg",
			vpc=vpc,
			port=config.get("frontend_port"),
			protocol=elb.ApplicationProtocol.HTTP,
			target_type=elb.TargetType.INSTANCE,
			health_check=elb.HealthCheck(path="/", healthy_http_codes="200")
		)

		put_ssm_parameter(self, f"/{prj_name}/{env_name}/targetgroup/frontend", self.frontend_tg.target_group_arn)

		self.backend_tg = elb.ApplicationTargetGroup(
			self, "BackendTargetGroup",
			target_group_name=f"{prj_name}-{env_name}-backend-tg",
			vpc=vpc,
			port=config.get("backend_port"),
			protocol=elb.ApplicationProtocol.HTTP,
			target_type=elb.TargetType.INSTANCE,
			health_check=elb.HealthCheck(path="/health", healthy_http_codes="200")
		)

		put_ssm_parameter(self, f"/{prj_name}/{env_name}/targetgroup/backend", self.backend_tg.target_group_arn)

		elb.ApplicationListenerRule(
			self,
			"FrontendRule",
			listener=https_listener,
			priority=10,
			conditions=[elb.ListenerCondition.host_headers([frontend_domain])],
			action=elb.ListenerAction.forward([self.frontend_tg])
		)

		elb.ApplicationListenerRule(
			self,
			"BackendRule",
			listener=https_listener,
			priority=20,
			conditions=[elb.ListenerCondition.host_headers([backend_domain])],
			action=elb.ListenerAction.forward([self.backend_tg])
		)

		for record_name in [frontend_domain, backend_domain]:
			route53.ARecord(
				self, f"{record_name.replace('.', '-')}-AliasRecord",
				zone=hosted_zone,
				record_name=record_name,
				target=route53.RecordTarget.from_alias(targets.LoadBalancerTarget(self.alb))
			)

