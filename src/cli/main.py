"""CLI entry point for the agent orchestrator."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

import yaml

from src.common.config import Config
from src.common.logging import configure_logging
from src.sdk.client import OrchestratorClient


SUCCESS = 0
ERROR = 1
USAGE_ERROR = 2


class ManifestError(ValueError):
    """Raised when a deploy manifest cannot be parsed as a mapping."""


class ManifestReadError(OSError):
    """Raised when a deploy manifest cannot be opened."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Orchestrator CLI")
    parser.add_argument("--config", "-c", help="Path to config file")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        help="Available commands",
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a new project",
    )
    init_parser.add_argument("name", help="Project name")

    deploy_parser = subparsers.add_parser("deploy", help="Deploy an agent")
    deploy_parser.add_argument("manifest", help="Path to agent manifest file")

    status_parser = subparsers.add_parser("status", help="Show agent status")
    status_parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="Watch mode",
    )

    logs_parser = subparsers.add_parser("logs", help="View agent logs")
    logs_parser.add_argument("agent_id", help="Agent ID")
    logs_parser.add_argument(
        "--tail",
        "-t",
        type=int,
        default=50,
        help="Number of lines",
    )

    return parser


def handle_init(args: argparse.Namespace) -> int:
    print(f"Initializing project: {args.name}")
    return SUCCESS


def load_manifest(manifest: str) -> Dict[str, Any]:
    path = Path(manifest)
    try:
        manifest_file = path.open(encoding="utf-8")
    except OSError as exc:
        raise ManifestReadError(str(exc)) from exc

    with manifest_file:
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                data = yaml.safe_load(manifest_file)
            except yaml.YAMLError as exc:
                raise ManifestError(f"invalid YAML manifest: {exc}") from exc
        else:
            try:
                data = json.load(manifest_file)
            except json.JSONDecodeError as exc:
                raise ManifestError(f"invalid JSON manifest: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a JSON/YAML object")
    return data


def deploy_manifest(
    manifest: str,
    config: Optional[Config] = None,
    client_factory: Callable[..., OrchestratorClient] = OrchestratorClient,
) -> Dict[str, Any]:
    payload = load_manifest(manifest)
    config = config or Config()
    client = client_factory(
        base_url=config.get("api.url"),
        api_key=config.get("api.key"),
    )
    return client.deploy_agent(payload)


def handle_deploy(
    args: argparse.Namespace,
    deploy_backend: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    config: Optional[Config] = None,
    client_factory: Callable[..., OrchestratorClient] = OrchestratorClient,
) -> int:
    try:
        if deploy_backend is None:
            result = deploy_manifest(
                args.manifest,
                config=config,
                client_factory=client_factory,
            )
        else:
            result = deploy_backend(args.manifest)
    except ManifestReadError as exc:
        print(
            f"Deploy failed: could not read manifest: {exc}",
            file=sys.stderr,
        )
        return USAGE_ERROR
    except ManifestError as exc:
        print(f"Deploy failed: {exc}", file=sys.stderr)
        return USAGE_ERROR
    except Exception as exc:
        print(f"Deploy failed: {exc}", file=sys.stderr)
        return ERROR

    if isinstance(result, dict) and result.get("error"):
        detail = result.get("message") or result["error"]
        print(
            f"Deploy failed: orchestrator rejected deployment: {detail}",
            file=sys.stderr,
        )
        return ERROR

    deployment_id = result.get("id") if isinstance(result, dict) else None
    if deployment_id:
        print(f"Deploy succeeded: {deployment_id}")
    else:
        print(f"Deploying agent from manifest: {args.manifest}")
    return SUCCESS


def handle_status(args: argparse.Namespace) -> int:
    print("Checking agent status...")
    return SUCCESS


def handle_logs(args: argparse.Namespace) -> int:
    print(f"Fetching logs for agent: {args.agent_id}")
    return SUCCESS


def dispatch_command(
    args: argparse.Namespace,
    config: Optional[Config] = None,
    client_factory: Callable[..., OrchestratorClient] = OrchestratorClient,
) -> int:
    handlers = {
        "init": handle_init,
        "status": handle_status,
        "logs": handle_logs,
    }
    if args.command == "deploy":
        return handle_deploy(
            args,
            config=config,
            client_factory=client_factory,
        )

    handler = handlers.get(args.command)
    if handler is None:
        return USAGE_ERROR
    return handler(args)


def cli(
    argv: Optional[Sequence[str]] = None,
    client_factory: Callable[..., OrchestratorClient] = OrchestratorClient,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        configure_logging("DEBUG")
    else:
        configure_logging("INFO")

    if args.command is None:
        parser.print_help()
        return USAGE_ERROR

    config = Config(args.config)
    return dispatch_command(
        args,
        config=config,
        client_factory=client_factory,
    )


if __name__ == "__main__":
    sys.exit(cli())

# 2019-01-03T18:44:00 update

# 2019-01-15T19:36:16 update

# 2019-02-15T12:13:23 update

# 2019-03-18T20:23:13 update

# 2019-03-22T09:42:46 update

# 2019-03-25T09:42:45 update

# 2019-07-16T18:56:48 update

# 2019-07-25T19:52:16 update

# 2019-08-18T18:35:47 update

# 2019-10-08T08:27:44 update

# 2019-11-05T14:16:14 update

# 2019-12-06T15:08:55 update

# 2020-01-15T12:28:12 update

# 2020-02-18T12:59:12 update

# 2020-03-18T18:36:09 update

# 2020-03-31T11:11:42 update

# 2020-06-16T08:24:25 update

# 2020-07-08T18:35:39 update

# 2020-12-09T10:37:56 update

# 2020-12-18T09:38:50 update

# 2020-12-29T13:08:30 update

# 2021-01-01T10:07:30 update

# 2021-01-19T16:42:27 update

# 2021-03-04T16:47:19 update

# 2021-06-25T09:17:23 update

# 2021-06-30T09:57:21 update

# 2021-10-14T19:11:31 update

# 2021-10-28T12:40:28 update

# 2021-11-29T14:09:58 update

# 2021-12-09T08:29:48 update

# 2021-12-14T12:25:33 update

# 2021-12-17T08:11:09 update

# 2022-01-05T12:27:12 update

# 2022-01-05T17:17:05 update

# 2022-02-25T13:48:23 update

# 2022-04-15T08:25:05 update

# 2022-07-13T19:24:38 update

# 2022-09-02T17:41:54 update

# 2022-12-17T16:02:25 update

# 2023-03-09T09:50:27 update

# 2023-04-10T10:37:23 update

# 2023-06-01T10:30:02 update

# 2023-06-27T09:30:48 update

# 2023-08-04T08:53:47 update

# 2023-09-29T20:24:53 update

# 2023-10-25T18:53:52 update

# 2023-12-04T15:52:41 update

# 2024-01-03T09:27:19 update

# 2024-03-07T17:47:20 update

# 2024-04-08T19:24:37 update

# 2024-06-10T10:00:24 update

# 2024-08-07T19:47:04 update

# 2024-09-17T14:57:37 update

# 2024-10-02T09:59:06 update

# 2024-12-10T17:02:51 update

# 2025-01-17T08:55:36 update

# 2025-02-27T18:17:16 update

# 2025-05-07T13:33:58 update

# 2025-05-31T17:12:56 update

# 2025-06-03T15:53:08 update

# 2026-01-28T11:15:32 update

# 2026-03-21T19:53:15 update

# 2026-05-06T09:09:51 update
