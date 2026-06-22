#!/usr/bin/env python3
"""
Identify manually-created AWS resources across all regions, printing the
JSON report to stdout.

Logic mirrors the Lambda version:
  1. Query Resource Explorer (aggregator index) for resources that do NOT
     carry a CloudFormation stack tag.
  2. Drop resources that are IaC-managed (Pulumi / Terraform / CFN tags) or
     match AWS "default / managed" heuristics.
  3. Print the survivors (likely manually-created) as JSON to stdout.

Output is a CANDIDATE list - heuristics are not authoritative; review it.

Caveats:
  * Resource Explorer Search returns at most 1000 results; larger accounts
    need a narrower --view-arn or per-region / per-service queries.
  * Only resource types indexed by Resource Explorer are visible, and the
    tag-based IaC detection only works when tags are indexed by RE.

Usage:
  ./manual_resource_finder.py                 # full report to stdout
  ./manual_resource_finder.py --arns-only     # just the ARNs, one per line
  ./manual_resource_finder.py --view-arn ...  # use a specific RE view
  AWS_PROFILE=prod ./manual_resource_finder.py > report.json
"""

import argparse
import datetime
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import boto3

# Resource Explorer reports global-service resources under this pseudo-region;
# it has no regional EC2 endpoint, so default-network lookups must skip it.
NON_REGIONAL = {"global", ""}
MAX_REGION_WORKERS = 12

IAC_TAG_KEYS = [
    "aws:cloudformation:stack-id",
    "aws:cloudformation:stack-name",
    "aws:cloudformation:logical-id",
]
IAC_TAG_PATTERNS = [
    re.compile(r"^pulumi:", re.I),
    re.compile(r"^aws:cloudformation:", re.I),
    re.compile(r"terraform", re.I),
    re.compile(r"^zdt:cloudbender", re.I),
    re.compile(r"^Conglomerate", re.I),
]
# A "managed by" tag only signals IaC when its value names a known tool,
# so a human-oriented "ManagedBy: platform-team" is not mistaken for IaC.
MANAGED_BY_KEY = re.compile(r"managed[_-]?by", re.I)
IAC_VALUE_PATTERNS = [
    re.compile(r"pulumi", re.I),
    re.compile(r"terraform", re.I),
    re.compile(r"cloudformation", re.I),
]

# Resource Explorer Search caps total results at 1000.
RE_RESULT_LIMIT = 1000
DEFAULT_ARN_PATTERNS = [
    re.compile(r":role/aws-service-role/"),
    re.compile(r":role/AWSServiceRoleFor", re.I),
    re.compile(r":role/aws-reserved/"),
    re.compile(r":policy/aws-service-role/", re.I),
    re.compile(r":alias/aws/"),
    re.compile(r"(parameter-group|option-group|subnet-group)/default", re.I),
    re.compile(r":instance-profile/aws-", re.I),
    # AWS-provided default singletons (one per account/region, not user-created).
    re.compile(r":event-bus/default$"),
    re.compile(r":workgroup/primary$"),
    re.compile(r":datacatalog/AwsDataCatalog$"),
    re.compile(r":sampling-rule/Default$"),
    re.compile(r":backup-vault:Default$"),
    re.compile(r":autoscalingconfiguration/DefaultConfiguration/"),
    re.compile(r":storage-lens/default-account-dashboard$"),
    re.compile(r":::cf-templates-[a-z0-9]+-"),
    # In-memory DB defaults (ElastiCache / MemoryDB).
    re.compile(r":user:default$"),
    re.compile(r":user/default$"),
    re.compile(r":acl/open-access$"),
    re.compile(r":parametergroup/default\."),
    # RDS default param/option/security groups use abbreviated, colon-separated ARNs.
    re.compile(r":(pg|cluster-pg|og|secgrp):default(\.|:|$)"),
    # Resource Explorer's own indexes / default view (this tool's own plumbing).
    re.compile(r":resource-explorer-2:.*:index/"),
    re.compile(r":resource-explorer-2:.*:view/default-view"),
    # IAM AWS-convention roles, SSO provider, and root MFA device.
    re.compile(r":saml-provider/AWSSSO_"),
    re.compile(r":role/(vmimport|aws-ec2-spot-fleet-tagging-role)$"),
    re.compile(r":mfa/root-account-mfa-device$"),
]
NETWORK_DEFAULT_TYPES = {
    "ec2:vpc",
    "ec2:subnet",
    "ec2:route-table",
    "ec2:internet-gateway",
    "ec2:security-group",
    "ec2:security-group-rule",
    "ec2:network-acl",
    "ec2:dhcp-options",
}


def log(msg):
    """Diagnostics go to stderr so stdout stays clean JSON."""
    print(msg, file=sys.stderr)


def is_iac_managed(tags):
    for t in tags:
        key, val = t.get("Key", ""), t.get("Value", "")
        if key in IAC_TAG_KEYS:
            return True
        if any(p.search(key) for p in IAC_TAG_PATTERNS):
            return True
        if MANAGED_BY_KEY.search(key) and any(
            p.search(val) for p in IAC_VALUE_PATTERNS
        ):
            return True
    return False


def is_default_by_arn(arn):
    return any(p.search(arn) for p in DEFAULT_ARN_PATTERNS)


def _region_default_ids(session, region):
    ec2 = session.client("ec2", region_name=region)
    region_ids = set()
    try:
        vpcs = ec2.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        )["Vpcs"]
    except Exception as e:
        log("  ! describe_vpcs failed in %s: %s" % (region, e))
        return region_ids
    for vpc in vpcs:
        vpc_id = vpc["VpcId"]
        region_ids.add(vpc_id)
        if vpc.get("DhcpOptionsId"):
            region_ids.add(vpc["DhcpOptionsId"])
        # Match only the AWS-created defaults, not every object users may
        # have added inside the default VPC.
        vpc_f = [{"Name": "vpc-id", "Values": [vpc_id]}]
        try:
            region_ids.update(
                s["SubnetId"]
                for s in ec2.describe_subnets(
                    Filters=vpc_f + [{"Name": "default-for-az", "Values": ["true"]}]
                )["Subnets"]
            )
            region_ids.update(
                r["RouteTableId"]
                for r in ec2.describe_route_tables(
                    Filters=vpc_f + [{"Name": "association.main", "Values": ["true"]}]
                )["RouteTables"]
            )
            region_ids.update(
                g["GroupId"]
                for g in ec2.describe_security_groups(
                    Filters=vpc_f + [{"Name": "group-name", "Values": ["default"]}]
                )["SecurityGroups"]
            )
            region_ids.update(
                n["NetworkAclId"]
                for n in ec2.describe_network_acls(
                    Filters=vpc_f + [{"Name": "default", "Values": ["true"]}]
                )["NetworkAcls"]
            )
            igws = ec2.describe_internet_gateways(
                Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
            )["InternetGateways"]
            region_ids.update(i["InternetGatewayId"] for i in igws)
        except Exception as e:
            log("  ! network describe failed for %s in %s: %s" % (vpc_id, region, e))
    region_ids.update(_default_sg_rule_ids(ec2, region))
    return region_ids


def _default_sg_rule_ids(ec2, region):
    """Rule IDs belonging to the AWS-created 'default' SG of every VPC."""
    rule_ids = set()
    try:
        sg_pages = ec2.get_paginator("describe_security_groups").paginate(
            Filters=[{"Name": "group-name", "Values": ["default"]}]
        )
        default_sg_ids = [g["GroupId"] for page in sg_pages for g in page["SecurityGroups"]]
        for i in range(0, len(default_sg_ids), 200):
            chunk = default_sg_ids[i : i + 200]
            rule_pages = ec2.get_paginator("describe_security_group_rules").paginate(
                Filters=[{"Name": "group-id", "Values": chunk}]
            )
            rule_ids.update(
                r["SecurityGroupRuleId"]
                for page in rule_pages
                for r in page["SecurityGroupRules"]
            )
    except Exception as e:
        log("  ! security-group-rule lookup failed in %s: %s" % (region, e))
    return rule_ids


def load_default_network_ids(session, regions):
    targets = sorted(r for r in regions if r not in NON_REGIONAL)
    if not targets:
        return set()
    default_ids = set()
    with ThreadPoolExecutor(max_workers=min(MAX_REGION_WORKERS, len(targets))) as pool:
        for region_ids in pool.map(
            lambda region: _region_default_ids(session, region), targets
        ):
            default_ids.update(region_ids)
    return default_ids


def arn_contains_any(arn, ids):
    return any(i in arn for i in ids)


def service_prefix(arn):
    """`arn:aws:rds:eu-central-1:...` -> `arn:aws:rds` (the grouping key)."""
    parts = arn.split(":")
    if len(parts) >= 3:
        return ":".join(parts[:3])
    return arn


def warn_if_not_aggregator(session):
    """Cross-region results require the client to hit the AGGREGATOR index."""
    rex = session.client("resource-explorer-2")
    try:
        idx = rex.get_index()
    except Exception as e:
        log("  ! could not verify Resource Explorer index: %s" % e)
        return
    if idx.get("Type") != "AGGREGATOR":
        log(
            "  ! WARNING: this region holds a %s index, not the AGGREGATOR; "
            "results may be limited to a single region. "
            "Pass --region <aggregator-region>." % idx.get("Type", "unknown")
        )


def collect(session, view_arn):
    rex = session.client("resource-explorer-2")
    paginator = rex.get_paginator("search")
    kwargs = {"QueryString": "-tag.key:aws:cloudformation:stack-id"}
    if view_arn:
        kwargs["ViewArn"] = view_arn

    raw = []
    regions_seen = set()
    for page in paginator.paginate(**kwargs):
        for r in page.get("Resources", []):
            raw.append(r)
            if r.get("Region"):
                regions_seen.add(r["Region"])
    return raw, regions_seen


def build_report(session, view_arn):
    log("Querying Resource Explorer (all regions)...")
    warn_if_not_aggregator(session)
    raw, regions_seen = collect(session, view_arn)
    log("  fetched %d resources across %d regions" % (len(raw), len(regions_seen)))
    if len(raw) >= RE_RESULT_LIMIT:
        log(
            "  ! WARNING: hit Resource Explorer's %d-result limit; scan is "
            "PARTIAL. Narrow with --view-arn or per-region / per-service "
            "queries." % RE_RESULT_LIMIT
        )

    log("Resolving default-VPC network objects...")
    default_net_ids = load_default_network_ids(session, regions_seen)
    log("  found %d default network IDs" % len(default_net_ids))

    candidates = []
    excluded = 0
    for r in raw:
        arn = r["Arn"]
        rtype = r.get("ResourceType", "")
        tags = [
            {"Key": p["Key"], "Value": p.get("Value", "")}
            for prop in r.get("Properties", [])
            if prop.get("Name") == "tags"
            for p in prop.get("Data", [])
        ]
        if (
            is_iac_managed(tags)
            or is_default_by_arn(arn)
            or (
                rtype in NETWORK_DEFAULT_TYPES
                and arn_contains_any(arn, default_net_ids)
            )
        ):
            excluded += 1
            continue
        candidates.append(
            {
                "arn": arn,
                "type": rtype,
                "region": r.get("Region"),
                "service": r.get("Service"),
                "tags": tags,
            }
        )

    grouped = {}
    for c in candidates:
        grouped.setdefault(service_prefix(c["arn"]), []).append(c)
    resources = {k: grouped[k] for k in sorted(grouped)}

    try:
        account = session.client("sts").get_caller_identity()["Account"]
    except Exception:
        account = None

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "account": account,
        "scanned": len(raw),
        "excluded_default_or_iac": excluded,
        "manual_candidates": len(candidates),
        "regions": sorted(regions_seen),
        "resources": resources,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Find manually-created AWS resources (excludes AWS defaults & IaC)."
    )
    ap.add_argument(
        "--view-arn", help="Resource Explorer view ARN (default view if omitted)"
    )
    ap.add_argument(
        "--arns-only",
        action="store_true",
        help="Print only candidate ARNs, one per line, instead of full JSON.",
    )
    ap.add_argument(
        "--region", help="Region for the Resource Explorer aggregator client."
    )
    ap.add_argument("--profile", help="AWS profile name.")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)

    try:
        report = build_report(session, args.view_arn)
    except Exception as e:
        log("ERROR: %s" % e)
        return 1

    if args.arns_only:
        for group in report["resources"].values():
            for r in group:
                print(r["arn"])
    else:
        print(json.dumps(report, indent=2))

    log(
        "Done: %d candidates, %d excluded."
        % (report["manual_candidates"], report["excluded_default_or_iac"])
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
