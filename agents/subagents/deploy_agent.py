"""
Deploy Agent - Specialized agent for deployment management.

Handles CDK/Terraform deployment, staging validation, canary deployments,
and blue-green rollout with automatic rollback.
"""

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime

import requests

from config import (
    load_config,
    CANARY_TRAFFIC_PERCENT,
    CANARY_DURATION_MINUTES,
    CANARY_ERROR_THRESHOLD,
)


# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format=json.dumps({
        "timestamp": "%(asctime)s",
        "level": "%(levelname)s",
        "message": "%(message)s",
    }),
)
logger = logging.getLogger(__name__)


@dataclass
class DeployResult:
    """Result of deployment."""
    status: str  # "success", "failed", "rolled_back"
    staging_url: str
    production_url: str
    deployment_id: str
    canary_metrics: Dict[str, float]
    duration: float
    error: Optional[str] = None


class CloudWatchClient:
    """AWS CloudWatch client for metrics monitoring."""

    def __init__(self, region: str = "us-east-1"):
        """Initialize CloudWatch client."""
        self.region = region

    def get_metrics(
        self,
        namespace: str,
        metric_name: str,
        duration_minutes: int = 5,
    ) -> Dict[str, float]:
        """Get CloudWatch metrics."""
        try:
            # Mock implementation - would use boto3 in production
            import random
            return {
                "error_rate": random.uniform(0, 2),
                "latency_p99": random.uniform(100, 500),
                "invocations": random.randint(100, 1000),
                "throttles": random.randint(0, 5),
            }
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_metrics",
                    "error": str(e),
                })
            )
            return {}


class DeployAgent:
    """Specialized agent for deployment management."""

    def __init__(self, config: Dict):
        """Initialize deploy agent."""
        self.config = config
        self.cloudwatch = CloudWatchClient()

    async def execute(
        self,
        merged_branch: str,
        repo_path: str,
    ) -> DeployResult:
        """Execute deployment: staging → canary → production."""
        logger.info(
            json.dumps({
                "action": "deploy_agent_execute",
                "branch": merged_branch,
            })
        )

        start_time = datetime.now()
        deployment_id = f"deploy_{int(time.time())}"

        try:
            # Validate CloudFormation
            if not self._validate_cdk(repo_path):
                raise RuntimeError("CDK validation failed")

            # Preview changes
            diff = self._get_cdk_diff(repo_path)
            logger.info(
                json.dumps({
                    "action": "execute",
                    "status": "diff_preview",
                    "changes": len(diff.split("\n")),
                })
            )

            # Deploy to staging
            staging_url = await self._deploy_to_staging(repo_path)
            logger.info(
                json.dumps({
                    "action": "execute",
                    "status": "staging_deployed",
                    "url": staging_url,
                })
            )

            # Run integration tests against staging
            if not await self._run_integration_tests(staging_url):
                raise RuntimeError("Staging integration tests failed")

            # Deploy canary to production
            canary_success, canary_metrics = await self._deploy_canary(repo_path)

            if not canary_success:
                # Rollback
                await self._rollback(repo_path, deployment_id)
                raise RuntimeError("Canary deployment failed, rolled back")

            # Monitor canary
            if not self._monitor_canary(canary_metrics):
                # Rollback
                await self._rollback(repo_path, deployment_id)
                raise RuntimeError("Canary monitoring failed, rolled back")

            # Promote canary to 100%
            production_url = await self._promote_canary(repo_path)

            duration = (datetime.now() - start_time).total_seconds()

            result = DeployResult(
                status="success",
                staging_url=staging_url,
                production_url=production_url,
                deployment_id=deployment_id,
                canary_metrics=canary_metrics,
                duration=duration,
            )

            logger.info(
                json.dumps({
                    "action": "deploy_agent_execute",
                    "status": "success",
                    "deployment_id": deployment_id,
                })
            )

            return result

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "deploy_agent_execute",
                    "error": str(e),
                })
            )
            duration = (datetime.now() - start_time).total_seconds()
            return DeployResult(
                status="failed",
                staging_url="",
                production_url="",
                deployment_id=deployment_id,
                canary_metrics={},
                duration=duration,
                error=str(e),
            )

    def _validate_cdk(self, repo_path: str) -> bool:
        """Validate CDK stack."""
        try:
            result = subprocess.run(
                ["cdk", "synth"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                logger.error(
                    json.dumps({
                        "action": "_validate_cdk",
                        "stderr": result.stderr[:500],
                    })
                )
                return False

            logger.info(
                json.dumps({
                    "action": "_validate_cdk",
                    "status": "success",
                })
            )
            return True

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_validate_cdk",
                    "error": str(e),
                })
            )
            return False

    def _get_cdk_diff(self, repo_path: str) -> str:
        """Get CDK diff to preview changes."""
        try:
            result = subprocess.run(
                ["cdk", "diff", "--no-color"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60,
            )

            return result.stdout
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_get_cdk_diff",
                    "error": str(e),
                })
            )
            return ""

    async def _deploy_to_staging(self, repo_path: str) -> str:
        """Deploy to staging environment."""
        try:
            result = subprocess.run(
                [
                    "cdk",
                    "deploy",
                    "--stage",
                    "staging",
                    "--require-approval",
                    "never",
                ],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=600,
            )

            if result.returncode != 0:
                raise RuntimeError(f"CDK deploy failed: {result.stderr}")

            # Extract endpoint from output
            staging_url = "https://staging.mvt.example.com"

            logger.info(
                json.dumps({
                    "action": "_deploy_to_staging",
                    "status": "success",
                    "url": staging_url,
                })
            )

            return staging_url

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_deploy_to_staging",
                    "error": str(e),
                })
            )
            raise

    async def _run_integration_tests(self, endpoint: str) -> bool:
        """Run integration tests against staging endpoint."""
        try:
            # Smoke test
            response = requests.get(endpoint, timeout=10)
            if response.status_code != 200:
                logger.error(
                    json.dumps({
                        "action": "_run_integration_tests",
                        "status": "failed",
                        "status_code": response.status_code,
                    })
                )
                return False

            logger.info(
                json.dumps({
                    "action": "_run_integration_tests",
                    "status": "passed",
                })
            )
            return True

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_run_integration_tests",
                    "error": str(e),
                })
            )
            return False

    async def _deploy_canary(self, repo_path: str) -> tuple[bool, Dict[str, float]]:
        """Deploy canary version to production with weighted routing."""
        try:
            # Deploy new version
            result = subprocess.run(
                [
                    "cdk",
                    "deploy",
                    "--stage",
                    "production",
                    "--require-approval",
                    "never",
                ],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=600,
            )

            if result.returncode != 0:
                raise RuntimeError(f"CDK deploy failed: {result.stderr}")

            # Configure Lambda alias weighted routing (10% to new version)
            self._configure_lambda_alias(
                traffic_new_version=CANARY_TRAFFIC_PERCENT,
            )

            logger.info(
                json.dumps({
                    "action": "_deploy_canary",
                    "status": "deployed",
                    "traffic_percent": CANARY_TRAFFIC_PERCENT,
                })
            )

            # Get initial metrics
            metrics = self.cloudwatch.get_metrics(
                namespace="AWS/Lambda",
                metric_name="Errors",
                duration_minutes=1,
            )

            return True, metrics

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_deploy_canary",
                    "error": str(e),
                })
            )
            return False, {}

    def _configure_lambda_alias(self, traffic_new_version: int) -> bool:
        """Configure Lambda alias weighted routing."""
        try:
            # Mock implementation - would use boto3 in production
            logger.info(
                json.dumps({
                    "action": "_configure_lambda_alias",
                    "traffic_new_version": traffic_new_version,
                })
            )
            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_configure_lambda_alias",
                    "error": str(e),
                })
            )
            return False

    def _monitor_canary(
        self,
        initial_metrics: Dict[str, float],
    ) -> bool:
        """Monitor canary for CANARY_DURATION_MINUTES."""
        try:
            logger.info(
                json.dumps({
                    "action": "_monitor_canary",
                    "duration_minutes": CANARY_DURATION_MINUTES,
                })
            )

            # Monitor for duration
            for minute in range(CANARY_DURATION_MINUTES):
                # Get current metrics
                current_metrics = self.cloudwatch.get_metrics(
                    namespace="AWS/Lambda",
                    metric_name="Errors",
                    duration_minutes=1,
                )

                error_rate = current_metrics.get("error_rate", 0)

                logger.info(
                    json.dumps({
                        "action": "_monitor_canary",
                        "minute": minute + 1,
                        "error_rate": error_rate,
                    })
                )

                # Check threshold
                if error_rate > CANARY_ERROR_THRESHOLD:
                    logger.error(
                        json.dumps({
                            "action": "_monitor_canary",
                            "status": "threshold_exceeded",
                            "error_rate": error_rate,
                            "threshold": CANARY_ERROR_THRESHOLD,
                        })
                    )
                    return False

                # Wait before next check
                time.sleep(60)

            logger.info(
                json.dumps({
                    "action": "_monitor_canary",
                    "status": "passed",
                })
            )
            return True

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_monitor_canary",
                    "error": str(e),
                })
            )
            return False

    async def _promote_canary(self, repo_path: str) -> str:
        """Promote canary to 100% traffic."""
        try:
            # Update Lambda alias to 100% new version
            self._configure_lambda_alias(traffic_new_version=100)

            logger.info(
                json.dumps({
                    "action": "_promote_canary",
                    "status": "promoted",
                    "traffic_percent": 100,
                })
            )

            return "https://mvt.example.com"

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_promote_canary",
                    "error": str(e),
                })
            )
            raise

    async def _rollback(self, repo_path: str, deployment_id: str) -> bool:
        """Rollback to previous version."""
        try:
            # Revert Lambda alias to previous version
            self._configure_lambda_alias(traffic_new_version=0)

            logger.warning(
                json.dumps({
                    "action": "_rollback",
                    "deployment_id": deployment_id,
                    "status": "rolled_back",
                })
            )

            return True

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_rollback",
                    "error": str(e),
                })
            )
            return False


async def main():
    """Main entry point for testing."""
    config = load_config()
    agent = DeployAgent(config)

    result = await agent.execute("main", "/repo")
    print(json.dumps(
        {
            "status": result.status,
            "staging_url": result.staging_url,
            "production_url": result.production_url,
            "deployment_id": result.deployment_id,
            "canary_metrics": result.canary_metrics,
            "duration": result.duration,
        },
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
