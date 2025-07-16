from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elb,
	aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_route53_targets as targets,
	Duration,
	RemovalPolicy
)
from constructs import Construct
import typing

class ALBStack(Stack):
	def __init__(
			self, scope: Construct, construct_id: str,
			vpc: ec2.Vpc,
			alb_sg: ec2.SecurityGroup,
			domain_name: str,
			frontend_subdomain: str,
			backend_subdomain: str,
			**kwargs
	) -> None:
		super().__init__(scope, construct_id, **kwargs)

		prj_name = self.node.try_get_context("project_name")
		env_name = self.node.try_get_context("env")

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

		http_listener = self.alb.add_listener(
			"HTTPListener",
			port=80,
			open=True
		)
		http_listener.add_action(
			"HttpRedirect",
			action=elb.ListenerAction.redirect(
				port="443",
				protocol="HTTPS"
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
		https_listener_interface = typing.cast(elb.IApplicationListener, https_listener)

		self.frontend_tg = elb.ApplicationTargetGroup(
			self, "FrontendTargetGroup",
			target_group_name=f"{prj_name}-{env_name}-frontend-tg",
			vpc=vpc,
			port=8002,
			protocol=elb.ApplicationProtocol.HTTP,
			target_type=elb.TargetType.INSTANCE,
			health_check=elb.HealthCheck(path="/", healthy_http_codes="200")
		)

		self.backend_tg = elb.ApplicationTargetGroup(
			self, "BackendTargetGroup",
			target_group_name=f"{prj_name}-{env_name}-backend-tg",
			vpc=vpc,
			port=3000,
			protocol=elb.ApplicationProtocol.HTTP,
			target_type=elb.TargetType.INSTANCE,
			health_check=elb.HealthCheck(path="/health", healthy_http_codes="200")
		)

		elb.ApplicationListenerRule(
			self,
			"FrontendRule",
			listener=https_listener_interface,
			priority=10,
			conditions=[elb.ListenerCondition.host_headers([frontend_domain])],
			action=elb.ListenerAction.forward([self.frontend_tg])
		)

		elb.ApplicationListenerRule(
			self,
			"BackendRule",
			listener=https_listener_interface,
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