from aws_cdk import (
	Stack,
	aws_ec2 as ec2,
)
from constructs import Construct

class SecurityGroupStack(Stack):
	def __init__(self, scope: Construct, construct_id:str, vpc:ec2.Vpc, **kwargs)->None:
		super().__init__(scope, construct_id, **kwargs)

		prj_name = self.node.try_get_context("project_name")
		env_name = self.node.try_get_context("env")

		# ALB SG
		self.alb_sg = ec2.SecurityGroup(self, "ALBSecurityGroup",
								   security_group_name=f"{prj_name}-{env_name}-alb-sg",
								   vpc = vpc,
								   description="Security group for ALB",
								   allow_all_outbound=True
								   )
		self.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "Allow HTTP traffic")
		self.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "Allow HTTPS traffic")

		# Frontend SG
		self.frontend_sg = ec2.SecurityGroup(self, "FrontendSecurityGroup",
									   security_group_name=f"{prj_name}-{env_name}-frontend-sg",
									   vpc = vpc,
									   description="Security group for frontend services",
									   allow_all_outbound=True
									   )
		self.frontend_sg.add_ingress_rule(self.alb_sg, ec2.Port.tcp(80), "Allow HTTP traffic from ALB")
		self.frontend_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "Allow SSH traffic from anywhere")

		# Backend SG
		self.backend_sg = ec2.SecurityGroup(self, "BackendSecurityGroup",
									  security_group_name=f"{prj_name}-{env_name}-backend-sg",
									  vpc = vpc,
									  description="Security group for backend services",
									  allow_all_outbound=True
									  )
		self.backend_sg.add_ingress_rule(self.alb_sg, ec2.Port.tcp(80), "Allow HTTP traffic from ALB")
		self.backend_sg.add_ingress_rule(self.frontend_sg, ec2.Port.tcp(3000), "Allow traffic from frontend")
		self.backend_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "Allow SSH traffic from anywhere")

		# Database SG
		self.db_sg = ec2.SecurityGroup(self, "DatabaseSecurityGroup",
								  security_group_name=f"{prj_name}-{env_name}-db-sg",
								  vpc = vpc,
								  description="Security group for database",
								  allow_all_outbound=True
								  )
		self.db_sg.add_ingress_rule(self.backend_sg, ec2.Port.tcp(5432), "Allow PostsgreSQL traffic from backend")