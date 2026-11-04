"""
Zero-Config AWS SAM CloudFormation -> Moto Test Harness & Isolated Lambda Loader.

Why this exists:
1. Prevents "Lying MagicMock" bugs where unit tests mock `boto3` table methods and miss
   real DynamoDB `KeySchema` mismatches, `Float types are not supported` (`Decimal` bugs),
   or invalid `KeyConditionExpression` queries.
2. Eliminates the 25-line `sys.path` / `del sys.modules["adapters"]` boilerplate at the
   top of multi-Lambda microservice test files.
"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator
from unittest.mock import patch as _stdlib_patch

import boto3
import yaml
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parents[2]


class _CloudFormationYamlLoader(yaml.SafeLoader):
    """YAML SafeLoader that preserves AWS CloudFormation intrinsic tags (`!Ref`, `!Sub`, etc.)."""

    yaml_constructors = yaml.SafeLoader.yaml_constructors.copy()
    yaml_multi_constructors: dict[Any, Any] = {}


def _construct_cfn_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> Any:
    tag_key = f"!{tag_suffix}"
    if isinstance(node, yaml.ScalarNode):
        return {tag_key: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_key: loader.construct_sequence(node)}
    if isinstance(node, yaml.MappingNode):
        return {tag_key: loader.construct_mapping(node)}
    return None


_CloudFormationYamlLoader.add_multi_constructor("!", _construct_cfn_tag)


@dataclasses.dataclass
class SamMotoEnvironment:
    """Container holding provisioned Moto clients, physical resource names, and resolved env vars."""

    app_name: str
    region_name: str
    account_id: str = "123456789012"
    environment: str = "dev"
    tables_by_logical_id: dict[str, str] = dataclasses.field(default_factory=dict)
    buckets_by_logical_id: dict[str, str] = dataclasses.field(default_factory=dict)
    queues_by_logical_id: dict[str, str] = dataclasses.field(default_factory=dict)
    ssm_parameters: dict[str, str] = dataclasses.field(default_factory=dict)
    environment_variables: dict[str, str] = dataclasses.field(default_factory=dict)

    @property
    def dynamodb_resource(self):
        return boto3.resource("dynamodb", region_name=self.region_name)

    @property
    def dynamodb_client(self):
        return boto3.client("dynamodb", region_name=self.region_name)

    @property
    def s3_client(self):
        return boto3.client("s3", region_name=self.region_name)

    @property
    def sqs_client(self):
        return boto3.client("sqs", region_name=self.region_name)

    @property
    def ssm_client(self):
        return boto3.client("ssm", region_name=self.region_name)

    def get_table(self, logical_id_or_env_var: str):
        """Return a boto3 DynamoDB Table resource by CloudFormation Logical ID or env var name."""
        physical_name = (
            self.tables_by_logical_id.get(logical_id_or_env_var)
            or self.environment_variables.get(logical_id_or_env_var)
            or logical_id_or_env_var
        )
        return self.dynamodb_resource.Table(physical_name)


def _resolve_cfn_value(
    value: Any,
    *,
    stack_name: str,
    region_name: str,
    account_id: str,
    environment: str,
    parameters: dict[str, str],
    logical_id_to_physical: dict[str, str],
) -> str:
    """Resolve CloudFormation `!Ref`, `!Sub`, `!GetAtt`, and `!If` expressions into strings."""
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, str):
        if "${" not in value:
            return value
        value = {"!Sub": value}
    if isinstance(value, dict):
        if "!Ref" in value:
            ref_target = str(value["!Ref"])
            if ref_target == "AWS::StackName":
                return stack_name
            if ref_target == "AWS::Region":
                return region_name
            if ref_target == "AWS::AccountId":
                return account_id
            if ref_target in logical_id_to_physical:
                return logical_id_to_physical[ref_target]
            if ref_target in parameters:
                return parameters[ref_target]
            return f"{stack_name}-{ref_target}"

        if "!Sub" in value:
            sub_expr = value["!Sub"]
            template_str = sub_expr[0] if isinstance(sub_expr, list) and sub_expr else str(sub_expr)

            def _replace_var(match: re.Match) -> str:
                token = match.group(1)
                if token == "AWS::StackName":
                    return stack_name
                if token == "AWS::Region":
                    return region_name
                if token == "AWS::AccountId":
                    return account_id
                if token == "Environment":
                    return environment
                if "." in token:
                    base, attr = token.split(".", 1)
                    resolved_base = logical_id_to_physical.get(base, f"{stack_name}-{base}")
                    if attr == "Arn":
                        return f"arn:aws:mock:{region_name}:{account_id}:{resolved_base}"
                    return resolved_base
                if token in logical_id_to_physical:
                    return logical_id_to_physical[token]
                if token in parameters:
                    return parameters[token]
                return f"{stack_name}-{token}"

            return re.sub(r"\$\{([^}]+)\}", _replace_var, template_str)

        if "!GetAtt" in value:
            get_att = value["!GetAtt"]
            if isinstance(get_att, list) and len(get_att) >= 2:
                base, attr = str(get_att[0]), str(get_att[1])
            elif isinstance(get_att, str) and "." in get_att:
                base, attr = get_att.split(".", 1)
            else:
                base, attr = str(get_att), "Arn"
            resolved_base = logical_id_to_physical.get(base, f"{stack_name}-{base}")
            if attr == "Arn":
                return f"arn:aws:mock:{region_name}:{account_id}:{resolved_base}"
            return resolved_base

        if "!If" in value:
            if_seq = value["!If"]
            if isinstance(if_seq, list) and len(if_seq) == 3:
                cond_name = str(if_seq[0])
                chosen = if_seq[1] if (cond_name == "IsProd" and environment == "prod") else if_seq[2]
                return _resolve_cfn_value(
                    chosen,
                    stack_name=stack_name,
                    region_name=region_name,
                    account_id=account_id,
                    environment=environment,
                    parameters=parameters,
                    logical_id_to_physical=logical_id_to_physical,
                )

    return str(value)


def provision_sam_resources_in_moto(
    app_name: str,
    *,
    extra_apps: list[str] | None = None,
    region_name: str = "ca-central-1",
    environment: str = "dev",
    account_id: str = "123456789012",
    repo_root: Path = REPO_ROOT,
) -> SamMotoEnvironment:
    """
    Parse `apps/<app_name>/template.yaml` (plus any `extra_apps`) and provision all
    DynamoDB tables, S3 buckets, SQS queues, and SSM parameters inside an active Moto context.
    """
    state = SamMotoEnvironment(
        app_name=app_name,
        region_name=region_name,
        account_id=account_id,
        environment=environment,
    )

    ddb_client = state.dynamodb_client
    s3_client = state.s3_client
    sqs_client = state.sqs_client
    ssm_client = state.ssm_client

    target_apps = list(dict.fromkeys([*(extra_apps or []), app_name]))

    for current_app in target_apps:
        template_path = repo_root / "apps" / current_app / "template.yaml"
        if not template_path.exists():
            raise FileNotFoundError(f"SAM template not found at {template_path}")

        tpl = yaml.load(template_path.read_text(encoding="utf-8"), Loader=_CloudFormationYamlLoader) or {}
        stack_name = f"{current_app}-{environment}"

        # 1. Extract Parameter defaults
        parameters: dict[str, str] = {"Environment": environment}
        for param_id, param_def in (tpl.get("Parameters") or {}).items():
            if not isinstance(param_def, dict):
                continue
            default_val = str(param_def.get("Default", f"mock-{param_id}"))
            param_type = str(param_def.get("Type", ""))
            # If this is an SSM Parameter reference (`AWS::SSM::Parameter::Value<String>`) and
            # an earlier app in `extra_apps` already wrote that SSM parameter, resolve its value!
            if "SSM::Parameter::Value" in param_type and default_val in state.ssm_parameters:
                parameters[param_id] = state.ssm_parameters[default_val]
            else:
                parameters[param_id] = default_val

        resources = tpl.get("Resources") or {}
        logical_id_to_physical: dict[str, str] = {}

        # 2. Provision DynamoDB tables with exact KeySchema, AttributeDefinitions, and GSIs
        for logical_id, res in resources.items():
            if not isinstance(res, dict):
                continue
            res_type = res.get("Type")
            props = res.get("Properties") or {}

            if res_type in ("AWS::DynamoDB::Table", "AWS::Serverless::SimpleTable"):
                raw_table_name = props.get("TableName")
                if raw_table_name:
                    table_name = _resolve_cfn_value(
                        raw_table_name,
                        stack_name=stack_name,
                        region_name=region_name,
                        account_id=account_id,
                        environment=environment,
                        parameters=parameters,
                        logical_id_to_physical=logical_id_to_physical,
                    )
                else:
                    table_name = f"{stack_name}-{logical_id}"

                if res_type == "AWS::Serverless::SimpleTable":
                    pk_def = props.get("PrimaryKey") or {"Name": "id", "Type": "String"}
                    pk_name = pk_def.get("Name", "id")
                    pk_type = "S" if pk_def.get("Type", "String") == "String" else pk_def.get("Type", "S")[0]
                    key_schema = [{"AttributeName": pk_name, "KeyType": "HASH"}]
                    attr_defs = [{"AttributeName": pk_name, "AttributeType": pk_type}]
                else:
                    key_schema = props.get("KeySchema") or []
                    attr_defs = props.get("AttributeDefinitions") or []

                create_kwargs: dict[str, Any] = {
                    "TableName": table_name,
                    "KeySchema": key_schema,
                    "AttributeDefinitions": attr_defs,
                    "BillingMode": "PAY_PER_REQUEST",
                }

                gsis = props.get("GlobalSecondaryIndexes")
                if gsis:
                    cleaned_gsis = []
                    for gsi in gsis:
                        cleaned_gsis.append(
                            {
                                "IndexName": gsi["IndexName"],
                                "KeySchema": gsi["KeySchema"],
                                "Projection": gsi.get("Projection", {"ProjectionType": "ALL"}),
                            }
                        )
                    create_kwargs["GlobalSecondaryIndexes"] = cleaned_gsis

                lsis = props.get("LocalSecondaryIndexes")
                if lsis:
                    cleaned_lsis = []
                    for lsi in lsis:
                        cleaned_lsis.append(
                            {
                                "IndexName": lsi["IndexName"],
                                "KeySchema": lsi["KeySchema"],
                                "Projection": lsi.get("Projection", {"ProjectionType": "ALL"}),
                            }
                        )
                    create_kwargs["LocalSecondaryIndexes"] = cleaned_lsis

                existing_tables = ddb_client.list_tables().get("TableNames", [])
                if table_name not in existing_tables:
                    ddb_client.create_table(**create_kwargs)

                state.tables_by_logical_id[logical_id] = table_name
                logical_id_to_physical[logical_id] = table_name

            elif res_type == "AWS::S3::Bucket":
                raw_bucket_name = props.get("BucketName")
                if raw_bucket_name:
                    bucket_name = _resolve_cfn_value(
                        raw_bucket_name,
                        stack_name=stack_name,
                        region_name=region_name,
                        account_id=account_id,
                        environment=environment,
                        parameters=parameters,
                        logical_id_to_physical=logical_id_to_physical,
                    )
                else:
                    bucket_name = f"{stack_name}-{logical_id}".lower()
                bucket_name = re.sub(r"[^a-z0-9.-]", "-", bucket_name.lower()).strip("-")[:63]

                existing_buckets = {b["Name"] for b in s3_client.list_buckets().get("Buckets", [])}
                if bucket_name not in existing_buckets:
                    if region_name == "us-east-1":
                        s3_client.create_bucket(Bucket=bucket_name)
                    else:
                        s3_client.create_bucket(
                            Bucket=bucket_name,
                            CreateBucketConfiguration={"LocationConstraint": region_name},
                        )

                state.buckets_by_logical_id[logical_id] = bucket_name
                logical_id_to_physical[logical_id] = bucket_name

            elif res_type == "AWS::SQS::Queue":
                raw_queue_name = props.get("QueueName")
                if raw_queue_name:
                    queue_name = _resolve_cfn_value(
                        raw_queue_name,
                        stack_name=stack_name,
                        region_name=region_name,
                        account_id=account_id,
                        environment=environment,
                        parameters=parameters,
                        logical_id_to_physical=logical_id_to_physical,
                    )
                else:
                    queue_name = f"{stack_name}-{logical_id}"

                q_resp = sqs_client.create_queue(QueueName=queue_name)
                queue_url = q_resp["QueueUrl"]
                state.queues_by_logical_id[logical_id] = queue_url
                logical_id_to_physical[logical_id] = queue_url

        # 3. Provision exported SSM parameters so cross-stack consumers resolve automatically
        for logical_id, res in resources.items():
            if not isinstance(res, dict) or res.get("Type") != "AWS::SSM::Parameter":
                continue
            props = res.get("Properties") or {}
            ssm_name = _resolve_cfn_value(
                props.get("Name"),
                stack_name=stack_name,
                region_name=region_name,
                account_id=account_id,
                environment=environment,
                parameters=parameters,
                logical_id_to_physical=logical_id_to_physical,
            )
            ssm_val = _resolve_cfn_value(
                props.get("Value"),
                stack_name=stack_name,
                region_name=region_name,
                account_id=account_id,
                environment=environment,
                parameters=parameters,
                logical_id_to_physical=logical_id_to_physical,
            )
            if ssm_name:
                ssm_client.put_parameter(Name=ssm_name, Value=ssm_val or "mock-value", Type="String", Overwrite=True)
                state.ssm_parameters[ssm_name] = ssm_val
                logical_id_to_physical[logical_id] = ssm_name

        # 4. Resolve Lambda Environment.Variables for the primary app
        if current_app == app_name:
            raw_env_vars: dict[str, Any] = {}
            global_vars = (
                (((tpl.get("Globals") or {}).get("Function") or {}).get("Environment") or {}).get("Variables")
                or {}
            )
            raw_env_vars.update(global_vars)
            for res in resources.values():
                if isinstance(res, dict) and res.get("Type") in ("AWS::Serverless::Function", "AWS::Lambda::Function"):
                    fn_vars = (((res.get("Properties") or {}).get("Environment") or {}).get("Variables")) or {}
                    raw_env_vars.update(fn_vars)

            for env_key, raw_val in raw_env_vars.items():
                resolved_val = _resolve_cfn_value(
                    raw_val,
                    stack_name=stack_name,
                    region_name=region_name,
                    account_id=account_id,
                    environment=environment,
                    parameters=parameters,
                    logical_id_to_physical=logical_id_to_physical,
                )
                state.environment_variables[env_key] = resolved_val

    return state


@contextlib.contextmanager
def sam_moto_environment(
    app_name: str,
    *,
    extra_apps: list[str] | None = None,
    region_name: str = "ca-central-1",
    environment: str = "dev",
    env_overrides: dict[str, str] | None = None,
) -> Iterator[SamMotoEnvironment]:
    """
    Context manager that activates `moto.mock_aws()`, provisions all tables/buckets/queues/SSM
    from `apps/<app_name>/template.yaml`, and sets resolved Lambda environment variables.
    """
    saved_env = os.environ.copy()
    try:
        os.environ["AWS_DEFAULT_REGION"] = region_name
        os.environ["AWS_REGION"] = region_name
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"

        with mock_aws():
            state = provision_sam_resources_in_moto(
                app_name,
                extra_apps=extra_apps,
                region_name=region_name,
                environment=environment,
            )
            for k, v in state.environment_variables.items():
                os.environ[k] = v
            if env_overrides:
                for k, v in env_overrides.items():
                    os.environ[k] = v
                    state.environment_variables[k] = v
            yield state
    finally:
        os.environ.clear()
        os.environ.update(saved_env)


_LOCAL_LAMBDA_SUBPACKAGES = (
    "actions",
    "adapters",
    "domain",
    "emails",
    "logic",
    "models",
    "routes",
    "helpers",
    "course_pruning",
)


_ISOLATED_MODULE_CACHE: dict[Path, tuple[ModuleType, dict[str, ModuleType]]] = {}


def load_isolated_lambda_module(
    lambda_file_path: str | Path,
    *,
    module_alias: str | None = None,
    reload_module: bool = False,
) -> ModuleType:
    """
    Import a Lambda entrypoint file (`apps/<app>/src/.../main.py`) with isolated resolution
    for its local sibling modules (`adapters`, `logic`, `models`, `domain`, etc.) without
    requiring manual `sys.path` or `del sys.modules[...]` boilerplate in test files.

    Caches the loaded module and its local sibling subpackages by resolved file path so
    calling `load_isolated_lambda_module(path)` inside a fixture restores `sys.path` and
    `sys.modules` to that Lambda's namespace without breaking `@patch(...)` target identity.
    """
    path = Path(lambda_file_path)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Lambda module file not found: {path}")

    lambda_dir = str(path.parent)
    sibling_names = {
        child.stem if child.is_file() else child.name
        for child in path.parent.iterdir()
        if not child.name.startswith(".") and child.name != "__pycache__"
    } | set(_LOCAL_LAMBDA_SUBPACKAGES)

    app_src_dir: Path | None = None
    extra_search_dirs: list[str] = [lambda_dir]
    for parent in path.parents:
        if parent.parent == REPO_ROOT / "apps":
            app_src_dir = parent / "src"
            extra_search_dirs.append(str(parent))
            layer_dir = parent / "src" / "layer" / "python"
            app_shared_dir = parent / "shared"
            if layer_dir.exists():
                extra_search_dirs.append(str(layer_dir))
            if app_shared_dir.exists():
                extra_search_dirs.append(str(app_shared_dir))
            break
    extra_search_dirs.append(str(REPO_ROOT / "common"))

    if app_src_dir is not None:
        allowed_set = set(extra_search_dirs) | {str(app_src_dir.parent)}
        sys.path[:] = [
            p
            for p in sys.path
            if not (str(app_src_dir) in os.path.abspath(p) and os.path.abspath(p) not in allowed_set)
        ]

    for search_dir in reversed(extra_search_dirs):
        if search_dir in sys.path:
            sys.path.remove(search_dir)
        sys.path.insert(0, search_dir)

    unique_module_name = module_alias or path.stem

    # Evict any other Lambda's local subpackages from sys.modules
    for mod_key in list(sys.modules.keys()):
        if any(
            mod_key == prefix or mod_key.startswith(f"{prefix}.")
            for prefix in sibling_names
        ):
            sys.modules.pop(mod_key, None)

    if path in _ISOLATED_MODULE_CACHE and not reload_module:
        cached_module, cached_submodules = _ISOLATED_MODULE_CACHE[path]
        sys.modules.update(cached_submodules)
        sys.modules[unique_module_name] = cached_module
        sys.modules[path.stem] = cached_module
        return cached_module

    importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(unique_module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create module spec for {path}")

    if path in _ISOLATED_MODULE_CACHE:
        # Reuse the same ModuleType object so top-level references stay valid
        module = _ISOLATED_MODULE_CACHE[path][0]
    else:
        module = importlib.util.module_from_spec(spec)

    sys.modules[unique_module_name] = module
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)

    captured_submodules = {
        mod_key: mod_val
        for mod_key, mod_val in sys.modules.items()
        if mod_val is not None
        and any(
            mod_key == prefix or mod_key.startswith(f"{prefix}.")
            for prefix in sibling_names
        )
    }
    _ISOLATED_MODULE_CACHE[path] = (module, captured_submodules)
    return module


def strict_patch(target: str, *args: Any, **kwargs: Any):
    """
    Drop-in wrapper around `unittest.mock.patch` that defaults to `autospec=True`
    when no replacement object (`new`), `new_callable`, or explicit `spec`/`autospec`
    argument is provided—preventing "lying MagicMock" signature drift bugs.
    """
    if (
        not args
        and "new" not in kwargs
        and "new_callable" not in kwargs
        and "spec" not in kwargs
        and "spec_set" not in kwargs
        and "autospec" not in kwargs
    ):
        kwargs["autospec"] = True
    return _stdlib_patch(target, *args, **kwargs)

