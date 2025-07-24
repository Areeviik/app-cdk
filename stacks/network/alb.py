from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elb,
	aws_certificatemanager as acm,
    aws_route53 as route53,
    aws_route53_targets as targets,
	Tags,
	Duration
)
from constructs import Construct
from typing import Dict, Any, List
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

		self.config = load_yaml(config_path)
		self.prj_name = self.config["project_name"]
		self.env_name = self.config["env"]

		self.vpcs: Dict[str, ec2.IVpc] = {}
		self.security_groups: Dict[str, ec2.ISecurityGroup] = {}
		self.hosted_zones: Dict[str, route53.IHostedZone] = {}
		self.certificates: Dict[str, acm.ICertificate] = {}
		self.target_groups: Dict[str, elb.ApplicationTargetGroup] = {}

		self._create_albs()

	def _create_albs(self):
		alb_configs = self.config.get("albs", [])

		if not alb_configs:
			print(f"WARNING: No ALB configurations found in the YAML file for {self.stack_name}.")
			return

		for alb_conf in alb_configs:
			self._create_single_alb(alb_conf)

	def _create_single_alb(self, alb_conf: Dict[str, Any]):
		alb_name_in_config = alb_conf["name"]
		vpc_key = alb_conf["vpc"]
		azs = alb_conf.get("availability_zones", [])
		sg_name = alb_conf["sg"]
		base_domain_name = alb_conf["domain_name"]

		vpc = self._get_vpc(vpc_key, azs)
		alb_sg = self._get_security_group(sg_name)
		alb_logical_id = f"{self.prj_name}-{self.env_name}-{alb_name_in_config}"
		cdk_resource_name = f"{self.prj_name}-{self.env_name}-{alb_name_in_config}"

		alb = elb.ApplicationLoadBalancer(
			self,
			alb_logical_id,
			load_balancer_name=cdk_resource_name,
			vpc=vpc,
			internet_facing=alb_conf.get("internet_facing", True),
			security_group=alb_sg,
			vpc_subnets=ec2.SubnetSelection(subnets=vpc.public_subnets)
		)

		Tags.of(alb).add("Project", self.prj_name)
		Tags.of(alb).add("Environment", self.env_name)
		Tags.of(alb).add("Name", cdk_resource_name)
		Tags.of(alb).add("AlbNameInConfig", alb_name_in_config)

		put_ssm_parameter(self, f"/{self.prj_name}/{self.env_name}/loadbalancer/{alb_name_in_config}",
						  alb.load_balancer_arn)


		certificate = self._get_or_create_certificate(alb_conf["certificate"], base_domain_name)

		http_redirect_conf = alb_conf.get("http_redirect_to_https", {})
		if http_redirect_conf.get("enabled", False):
			http_listener = alb.add_listener(
				f"{alb_name_in_config}HTTPListener",
				port=80,
				open=True
			)
			http_listener.add_action(
				f"{alb_name_in_config}-HTTPRedirect",
				action=elb.ListenerAction.redirect(
					protocol="HTTPS",
					port="443",
					host="#{host}",
					permanent=http_redirect_conf.get("permanent", True)
				)
			)
		else:
			print(
				f"INFO: HTTP to HTTPS redirect is disabled for ALB '{alb_name_in_config}'. No HTTP listener or redirect will be created.")

		https_listener = alb.add_listener(
			f"{alb_name_in_config}HTTPSListener",
			port=443,
			open=True,
			certificates=[certificate],
			default_action=self._resolve_listener_action(alb_conf.get("default_https_action",
																	  {"type": "fixed_response", "status_code": 404,
																	   "message_body": "Not found"}))
		)

		self._create_target_groups_and_rules(alb, https_listener, alb_conf)

	def _get_vpc(self, vpc_key: str, azs: List[str]) -> ec2.IVpc:
		if vpc_key not in self.vpcs:
			vpc_id_param_name = f"/{self.prj_name}/{self.env_name}/vpc/{vpc_key}"
			public_subnet_path_prefix = f"/{self.prj_name}/{self.env_name}/{vpc_key}/subnet/public"

			try:
				vpc_id = get_ssm_parameter(self, vpc_id_param_name)
				public_subnet_ids = get_ssm_subnet_ids(self, public_subnet_path_prefix, len(azs))

				self.vpcs[vpc_key] = ec2.Vpc.from_vpc_attributes(
					self,
					f"{vpc_key}VpcImport",
					vpc_id=vpc_id,
					availability_zones=azs,
					public_subnet_ids=public_subnet_ids
				)
			except Exception as e:
				raise ValueError(
					f"Failed to import VPC '{vpc_key}' (ID from SSM: {vpc_id_param_name}, Subnets from: {public_subnet_path_prefix}): {e}")
		return self.vpcs[vpc_key]

	def _get_security_group(self, sg_name: str) -> ec2.ISecurityGroup:
		if sg_name not in self.security_groups:
			sg_id_param_name = f"/{self.prj_name}/{self.env_name}/sg/{sg_name}"
			try:
				sg_id = get_ssm_parameter(self, sg_id_param_name)
				self.security_groups[sg_name] = ec2.SecurityGroup.from_security_group_id(
					self, f"{sg_name}SGImport", sg_id
				)
			except Exception as e:
				raise ValueError(f"Failed to import Security Group '{sg_name}' (ID from SSM: {sg_id_param_name}): {e}")
		return self.security_groups[sg_name]

	def _get_or_create_certificate(self, cert_conf: Dict[str, Any], base_domain_name: str) -> acm.ICertificate:
		cert_id = cert_conf["id"]

		if cert_id not in self.certificates:
			cert_type = cert_conf.get("type", "create")

			if cert_type == "create":
				primary_domain = f"{cert_conf['main_subdomain']}.{base_domain_name}"
				alt_names = [f"{s}.{base_domain_name}" for s in cert_conf.get("alt_subdomains", [])]

				hosted_zone_name = cert_conf.get("hosted_zone_name", base_domain_name)
				if hosted_zone_name not in self.hosted_zones:
					try:
						self.hosted_zones[hosted_zone_name] = route53.HostedZone.from_lookup(
							self, f"{hosted_zone_name.replace('.', '-')}-HZ", domain_name=hosted_zone_name
						)
					except Exception as e:
						raise ValueError(
							f"Failed to lookup Hosted Zone '{hosted_zone_name}' for certificate '{cert_id}': {e}")
				hosted_zone = self.hosted_zones[hosted_zone_name]

				certificate = acm.Certificate(
					self,
					f"{cert_id}Cert",
					certificate_name=f"{self.prj_name}-{self.env_name}-{cert_id}",
					domain_name=primary_domain,
					subject_alternative_names=alt_names,
					validation=acm.CertificateValidation.from_dns(hosted_zone),
				)
				self.certificates[cert_id] = certificate
			elif cert_type == "import":
				cert_arn = cert_conf.get("arn")
				if not cert_arn:
					raise ValueError(f"Certificate config for ID '{cert_id}' with type 'import' must provide an 'arn'.")
				self.certificates[cert_id] = acm.Certificate.from_certificate_arn(
					self, f"{cert_id}CertImport", cert_arn
				)
			else:
				raise ValueError(f"Unsupported certificate type '{cert_type}' for cert ID '{cert_id}'. Must be 'create' or 'import'.")

		return self.certificates[cert_id]

	def _create_target_groups_and_rules(self, alb: elb.ApplicationLoadBalancer, listener: elb.ApplicationListener,
										alb_conf: Dict[str, Any]):
		tg_rules_configs = alb_conf.get("target_groups", [])

		if not tg_rules_configs:
			print(f"WARNING: No target group configurations found for ALB '{alb_conf['name']}'.")
			return

		priority_counter = 10

		for i, tg_rule_conf in enumerate(tg_rules_configs):
			tg_name_in_config = tg_rule_conf["name"]
			port = tg_rule_conf["port"]
			protocol = getattr(elb.ApplicationProtocol, tg_rule_conf.get("protocol", "HTTP").upper())
			target_type = getattr(elb.TargetType, tg_rule_conf.get("target_type", "INSTANCE").upper())

			health_check_conf = tg_rule_conf.get("health_check", {})
			health_check = elb.HealthCheck(
				path=health_check_conf.get("path", "/"),
				healthy_http_codes=health_check_conf.get("healthy_http_codes", "200"),
				interval=Duration.seconds(health_check_conf.get("interval_seconds", 30)),
				timeout=Duration.seconds(health_check_conf.get("timeout_seconds", 5)),
			)

			tg_logical_id = f"{alb_conf['name']}{tg_name_in_config}TG"
			tg = elb.ApplicationTargetGroup(
				self,
				tg_logical_id,
				target_group_name=f"{alb_conf['name']}-{tg_name_in_config}",
				vpc=self.vpcs[alb_conf["vpc"]],
				port=port,
				protocol=protocol,
				target_type=target_type,
				health_check=health_check,
			)
			self.target_groups[tg_name_in_config] = tg

			put_ssm_parameter(
				self,
				f"/{self.prj_name}/{self.env_name}/targetgroup/{alb_conf['name']}-{tg_name_in_config}",
				tg.target_group_arn
			)

			conditions: List[elb.ListenerCondition] = []

			if "host_headers" in tg_rule_conf:
				host_headers = [f"{s}.{alb_conf['domain_name']}" for s in tg_rule_conf["host_headers"]]
				conditions.append(elb.ListenerCondition.host_headers(host_headers))

			if "path_patterns" in tg_rule_conf:
				conditions.append(elb.ListenerCondition.path_patterns(tg_rule_conf["path_patterns"]))

			for header_conf in tg_rule_conf.get("http_header_conditions", []):
				conditions.append(elb.ListenerCondition.http_header(header_conf["name"], header_conf["values"]))

			for query_conf in tg_rule_conf.get("query_string_conditions", []):
				conditions.append(elb.ListenerCondition.query_strings(
					[elb.QueryStringPair(key=query_conf["key"], value=query_conf["value"])]))

			if "source_ips" in tg_rule_conf:
				conditions.append(elb.ListenerCondition.source_ips(tg_rule_conf["source_ips"]))

			rule_action = tg_rule_conf.get("action", {"type": "forward", "target_group_name_ref": tg_name_in_config})

			if not conditions and rule_action["type"] != "fixed_response" and rule_action["type"] != "redirect":
				print(
					f"WARNING: Rule '{tg_name_in_config}' for ALB '{alb_conf['name']}' has no conditions. It might not be created as a rule or might act unexpectedly. Consider making it the default action.")
				continue

			elb.ApplicationListenerRule(
				self,
				f"{alb_conf['name']}Rule{i}",
				listener=listener,
				priority=priority_counter,
				conditions=conditions,
				action=self._resolve_listener_action(rule_action),
			)
			priority_counter += 10

			if "host_headers" in tg_rule_conf and tg_rule_conf["host_headers"]:
				hosted_zone_name = alb_conf.get("hosted_zone_name", alb_conf['domain_name'])
				if hosted_zone_name not in self.hosted_zones:
					try:
						self.hosted_zones[hosted_zone_name] = route53.HostedZone.from_lookup(
							self, f"{hosted_zone_name.replace('.', '-')}-HZ-Rule{i}", domain_name=hosted_zone_name
						)
					except Exception as e:
						print(
							f"ERROR: Failed to lookup Hosted Zone '{hosted_zone_name}' for rule '{tg_name_in_config}' ARecord: {e}")
						continue
				hosted_zone = self.hosted_zones[hosted_zone_name]

				for host_header in tg_rule_conf["host_headers"]:
					fqdn = f"{host_header}.{alb_conf['domain_name']}"
					record_logical_id = f"{fqdn.replace('.', '')}AliasRecord"
					route53.ARecord(
						self,
						record_logical_id,
						zone=hosted_zone,
						record_name=fqdn,
						target=route53.RecordTarget.from_alias(targets.LoadBalancerTarget(alb))
					)

	def _resolve_listener_action(self, action_conf: Dict[str, Any]) -> elb.ListenerAction:
		action_type = action_conf["type"]

		if action_type == "fixed_response":
			return elb.ListenerAction.fixed_response(
				status_code=action_conf["status_code"],
				content_type=action_conf.get("content_type"),
				message_body=action_conf.get("message_body"),
			)
		elif action_type == "forward":
			tg_name_ref = action_conf.get("target_group_name_ref")

			if not tg_name_ref:
				raise ValueError(f"Forward action requires 'target_group_name_ref' in config: {action_conf}")

			target_group = self.target_groups.get(tg_name_ref)
			if not target_group:
				print(f"WARNING: Target Group '{tg_name_ref}' not found in current stack's cache for forward action. Attempting to get its ARN from SSM.")
				tg_arn_param = f"/{self.prj_name}/{self.env_name}/targetgroup/{tg_name_ref}"
				try:
					tg_arn = get_ssm_parameter(self, tg_arn_param)
					target_group = elb.ApplicationTargetGroup.from_target_group_arn(
						self,
						f"ImportedTG-{tg_name_ref}",
						tg_arn
					)
				except Exception as e:
					raise ValueError(f"Could not resolve Target Group '{tg_name_ref}' for forward action: {e}. Ensure it's defined or its ARN is in SSM.")

			return elb.ListenerAction.forward(target_groups=[target_group])

		elif action_type == "redirect":
			return elb.ListenerAction.redirect(
				protocol=action_conf.get("protocol"),
				port=action_conf.get("port"),
				host=action_conf.get("host"),
				permanent=action_conf.get("permanent", False)
			)
		else:
			raise ValueError(f"Unsupported listener action type: {action_type}")