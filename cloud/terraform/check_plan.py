"""Checks a Terraform plan against the owner's rules before anyone applies it.

    terraform plan -out=perception.plan
    terraform show -json perception.plan > plan.json
    python3 check_plan.py plan.json

Fails unless every change creates one of the expected resource types, every name starts finepoint-perception, every
taggable resource carries project = finepoint-perception and a Name starting finepoint-perception, the security group
lets in only TCP 22 and 443, the role's only policy is finepoint-perception-s3, no Elastic IP or managed policy
appears, the only lookups are AWS's public catalogue, and no policy known at plan time has a delete action or
Resource "*". Policies that name the bucket are not known until apply (the bucket name gets a random suffix);
tests/server.tftest.hcl checks those. Standard library only.
"""
import json
import sys

PREFIX = "finepoint-perception"
ALLOWED_TYPES = {
    "aws_s3_bucket", "aws_s3_bucket_ownership_controls", "aws_s3_bucket_public_access_block", "aws_s3_bucket_versioning",
    "aws_s3_bucket_server_side_encryption_configuration", "aws_s3_bucket_lifecycle_configuration", "aws_s3_bucket_policy",
    "aws_s3_object", "aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_route_table", "aws_route_table_association",
    "aws_security_group", "aws_key_pair", "aws_iam_role", "aws_iam_instance_profile", "aws_iam_role_policy", "aws_instance",
    "aws_ec2_tag",
}
ALLOWED_DATA = {"archive_file", "aws_ssm_parameter", "aws_ec2_instance_type_offerings"}
ROLE_POLICIES = {"finepoint-perception-s3"}
INGRESS_PORTS = [22, 443]
NAME_KEYS = ("name", "name_prefix", "bucket_prefix", "key_name")


def as_list(v):
    return v if isinstance(v, list) else [v]


def check_policy(where, text, problems):
    for s in json.loads(text)["Statement"]:
        if not str(s.get("Sid", "")).startswith("FinepointPerception"):
            problems.append(f"{where}: statement Sid {s.get('Sid')!r} does not start FinepointPerception")
        actions = as_list(s.get("Action", []))
        problems += [f"{where}: delete action {a}" for a in actions if "delete" in a.lower()]
        if "*" in as_list(s.get("Resource", [])):
            problems.append(f"{where}: Resource \"*\" on {actions}")
        if s.get("Effect") == "Allow" and any("*" in a for a in actions):
            problems.append(f"{where}: wildcard action {actions}")


def ingress_ok(rules):
    return sorted(i["from_port"] for i in rules) == INGRESS_PORTS and all(
        i["to_port"] == i["from_port"] and i["protocol"] == "tcp" and i["cidr_blocks"] == ["0.0.0.0/0"]
        and not (i.get("ipv6_cidr_blocks") or i.get("prefix_list_ids") or i.get("security_groups") or i.get("self"))
        for i in rules)


def check(plan):
    problems, notes = [], []
    for r in plan.get("resource_changes", []):
        addr, ch = r["address"], r["change"]
        if r["mode"] == "data":
            continue
        if ch["actions"] != ["create"]:
            problems.append(f"{addr}: {ch['actions']}, only create is allowed")
        if r["type"] not in ALLOWED_TYPES:
            problems.append(f"{addr}: resource type {r['type']} is not part of this build")
        after, unknown = ch.get("after") or {}, ch.get("after_unknown") or {}
        for k in NAME_KEYS:
            if isinstance(after.get(k), str) and not after[k].startswith(PREFIX):
                problems.append(f"{addr}: {k} {after[k]!r} does not start {PREFIX}")
        if "tags_all" in after or "tags_all" in unknown:
            tags = after.get("tags_all") or {}
            if tags.get("project") != PREFIX or not str(tags.get("Name", "")).startswith(PREFIX):
                problems.append(f"{addr}: tags_all {tags} lacks project = {PREFIX} or a Name starting {PREFIX}")
        if r["type"] == "aws_ec2_tag" and after.get("key") in ("Name", "project") and not str(after.get("value")).startswith(PREFIX):
            problems.append(f"{addr}: tag {after.get('key')} = {after.get('value')!r}")
        if r["type"] == "aws_security_group" and not ingress_ok(after.get("ingress") or []):
            problems.append(f"{addr}: ingress {after.get('ingress')}, only TCP 22 and 443 from 0.0.0.0/0 are allowed")
        if r["type"] == "aws_iam_role_policy" and after.get("name") not in ROLE_POLICIES:
            problems.append(f"{addr}: role policy {after.get('name')!r}, the role's only policy is finepoint-perception-s3")
        if after.get("managed_policy_arns"):
            problems.append(f"{addr}: managed policies {after['managed_policy_arns']}")
        for k in ("policy", "assume_role_policy"):
            if isinstance(after.get(k), str):
                check_policy(f"{addr}.{k}", after[k], problems)
            elif unknown.get(k) and r["type"] != "aws_s3_bucket":
                notes.append(f"{addr}.{k}: known only after apply, checked by tests/server.tftest.hcl")
    for res in plan.get("configuration", {}).get("root_module", {}).get("resources", []):
        if res["mode"] == "data" and res["type"] not in ALLOWED_DATA:
            problems.append(f"data.{res['type']}.{res['name']}: reads something that already exists in the account")
        if res["mode"] == "data" and res["type"] == "aws_ssm_parameter":
            name = res.get("expressions", {}).get("name", {}).get("constant_value", "")
            if not name.startswith("/aws/service/"):
                problems.append(f"data.aws_ssm_parameter.{res['name']}: {name!r} is not an AWS public parameter")
    return problems, notes


def main():
    plan = json.load(open(sys.argv[1]) if len(sys.argv) > 1 else sys.stdin)
    problems, notes = check(plan)
    for n in notes:
        print("NOTE", n)
    for p in problems:
        print("FAIL", p)
    creates = sum(1 for r in plan.get("resource_changes", []) if r["mode"] == "managed")
    print(f"{'FAIL' if problems else 'PASS'}: {creates} resources to create, {len(problems)} problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
