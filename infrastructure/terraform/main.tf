# PulseTwin Infrastructure — stub, not applied
#
# Target: AWS EKS (module names below map 1:1 onto Azure AKS equivalents -- aks instead
# of eks, azurerm_postgresql_flexible_server instead of aws_db_instance -- the shape does
# not change, only the provider). This file is deliberately never `terraform apply`'d as
# part of the demo (finals brief SS5 anti-patterns: Docker Compose is the demo runtime).
# Its purpose is the same as infrastructure/kubernetes/*.yaml's: show that the path from
# "hackathon prototype" to "an enterprise IT team would approve this" has been thought
# through, not just asserted. Every resource here corresponds to something already
# decided elsewhere in this repo -- configs/deployment.yaml for sizing,
# infrastructure/kubernetes/network-policy.yaml for the OT/IT segmentation story,
# docs/PRODUCTION_ARCHITECTURE.md for the diagram this maps onto.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  # A real rollout points this at S3 + DynamoDB (or Terraform Cloud) for locking so two
  # engineers applying at once cannot corrupt state. Left as local backend here because
  # this configuration is never actually applied.
  # backend "s3" {}
}

variable "environment" {
  description = "deploy environment name, e.g. staging | production"
  type        = string
  default     = "production"
}

variable "plant_ids" {
  description = "Plants this deployment serves -- one EKS namespace + one RDS database per plant, matching CLAUDE.md's config-driven multi-plant model (plant_a.yaml, plant_b.yaml, ...)."
  type        = list(string)
  default     = ["plant_a", "plant_b"]
}

# ---------------------------------------------------------------------------- networking

module "vpc" {
  source = "terraform-aws-modules/vpc/aws"

  name = "pulsetwin-${var.environment}"
  cidr = "10.42.0.0/16"

  # OT/IT segmentation at the network layer, one level below the Kubernetes
  # NetworkPolicy in infrastructure/kubernetes/network-policy.yaml: private subnets carry
  # the data plane (backend, ingestion, postgres, mqtt); only the load balancer sits in a
  # public subnet. Nothing in the private subnets gets a public IP or a NAT-gateway-backed
  # default route to the internet -- configs/deployment.yaml's
  # network.internet_egress_allowed: false, enforced here rather than only in-cluster.
  azs             = ["${data.aws_region.current.name}a", "${data.aws_region.current.name}b"]
  private_subnets = ["10.42.1.0/24", "10.42.2.0/24"]
  public_subnets  = ["10.42.101.0/24", "10.42.102.0/24"]

  enable_nat_gateway = false  # no internet egress from the data plane, by policy
  enable_dns_hostnames = true
}

data "aws_region" "current" {}

# --------------------------------------------------------------------------------- eks

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = "pulsetwin-${var.environment}"
  cluster_version = "1.29"
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnets

  # Two node groups: a small always-on pool for cluster/system pods (CoreDNS, the
  # ingress controller), and the workload pool the backend/ingestion Deployments and the
  # backend HPA (infrastructure/kubernetes/hpa.yaml, 2-6 replicas) actually scale on.
  eks_managed_node_groups = {
    system = {
      instance_types = ["t3.medium"]
      min_size       = 2
      max_size       = 2
      desired_size   = 2
    }
    workload = {
      instance_types = ["t3.large"]
      min_size       = 2
      max_size       = 6   # matches configs/deployment.yaml services.backend.replicas.max
      desired_size   = 2
    }
  }
}

# --------------------------------------------------------------------------------- rds

resource "aws_db_subnet_group" "pulsetwin" {
  name       = "pulsetwin-${var.environment}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_db_instance" "pulsetwin" {
  for_each = toset(var.plant_ids)

  identifier     = "pulsetwin-${each.key}-${var.environment}"
  engine         = "postgres"
  engine_version = "16"
  # configs/deployment.yaml services.postgres.resources -- the demo's single-node
  # db.t3.small-equivalent sizing; production would size per plant's actual read volume,
  # not copy the demo's number, but this stub keeps the ratio explicit rather than
  # inventing an unsourced larger figure.
  instance_class      = "db.t3.medium"
  allocated_storage    = 50  # GB; configs/deployment.yaml's storage_gb: 20 is the demo's
                              # single SQLite-replacement volume, scaled up here for a
                              # per-plant production database with room for the audit log's
                              # ADR-034 foreign-key-enforced multi-year retention.
  multi_az             = true   # HA -- docs/SECURITY.md and docs/INCIDENT_RUNBOOK.md's
                                 # "database connection failure" runbook assumes automatic
                                 # failover, which is what multi_az actually buys.
  storage_encrypted    = true   # docs/SECURITY.md: AES-256 at rest.
  db_subnet_group_name = aws_db_subnet_group.pulsetwin.name
  db_name              = "pulsetwin_${each.key}"
  username             = "pulsetwin"
  # Never a literal password in state -- see docs/SECURITY.md's secrets-management
  # section (Vault/External Secrets Operator in production, not Terraform-managed
  # plaintext). manage_master_user_password delegates the actual secret to AWS Secrets
  # Manager and rotates it independently of any `terraform apply`.
  manage_master_user_password = true

  backup_retention_period = 7
  deletion_protection     = var.environment == "production"

  tags = {
    "pulsetwin:plant"       = each.key
    "pulsetwin:environment" = var.environment
  }
}

# ------------------------------------------------------------------------ observability

# infrastructure/monitoring/prometheus.yml + grafana-dashboards/pulsetwin-overview.json
# are the actual scrape config and dashboard (checked in, not templated here). This
# module represents standing up managed Prometheus/Grafana (AWS Managed Grafana / AMP)
# rather than self-hosting them in-cluster, which is the production-appropriate choice
# once the finals-brief anti-pattern ("do not build a Grafana dashboard that runs in the
# demo") stops applying and this actually ships.
# module "observability" {
#   source = "terraform-aws-modules/managed-prometheus/aws"
# }
