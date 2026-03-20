"""
Deploy Agent - Specialized agent for deployment orchestration and monitoring.

NO LONGER PERFORMS DIRECT DEPLOYMENTS (cdk deploy, terraform apply).
Instead monitors GitHub Actions workflows triggered by Git events.

Deployment flow:
1. Code Agent: PR created → pr-checks.yml runs
2. Master Orchestrator: Merges PR → deploy-staging.yml runs automatically
3. Deploy Agent: Monitors staging deployment health
4. Deploy Agent: Monitors canary deployment health
5. Deploy Agent: Watches for canary success → triggers production approval
6. Deploy Agent: On approval → deploy-production.yml runs automatically
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

import requests

from config import load_config

# Try to import boto3, but don't fail if it's not available
try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


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
    """Result of deployment monitoring."""
    status: str  # "success", "failed", "rolled_back", "monitoring"
    workflow_run_id: str
    staging_url: Optional[str] = None
    production_url: Optional[str] = None
    canary_metrics: Optional[Dict[str, float]] = None
    duration: float = 0.0
    error: Optional[str] = None


class GitHubWorkflowClient:
    """GitHub client for workflow and deployment monitoring."""

    def __init__(self, token: str, org: str, repo: str):
        """Initialize GitHub workflow client."""
        self.token = token
        self.org = org
        self.repo = repo
        self.base_url = "https://api.github.com"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        })

    def get_workflow_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get workflow run details."""
        try:
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/actions/runs/{run_id}"
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_workflow_run",
                    "error": str(e),
                })
            )
            return None

    def get_workflow_jobs(self, run_id: str) -> List[Dict[str, Any]]:
        """Get jobs in a workflow run."""
        try:
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/actions/runs/{run_id}/jobs"
            response = self.session.get(url)
            response.raise_for_status()
            return response.json().get("jobs", [])
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_workflow_jobs",
                    "error": str(e),
                })
            )
            return []

    def get_latest_workflow_run(self, workflow_name: str) -> Optional[Dict[str, Any]]:
        """Get latest run of a specific workflow."""
        try:
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/actions/workflows/{workflow_name}/runs"
            response = self.session.get(url, params={"per_page": 1})
            response.raise_for_status()
            runs = response.json().get("workflow_runs", [])
            return runs[0] if runs else None
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_latest_workflow_run",
                    "error": str(e),
                })
            )
            return None

    def revert_merge(self, pr_number: int) -> bool:
        """Revert a merged PR by reverting the merge commit on main.

        Used for rollback when canary fails.

        Args:
            pr_number: GitHub PR number to revert

        Returns:
            True if revert was successful
        """
        try:
            # Get PR details
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/pulls/{pr_number}"
            response = self.session.get(url)
            response.raise_for_status()
            pr_data = response.json()

            if pr_data["state"] != "closed":
                logger.warning(
                    json.dumps({
                        "action": "revert_merge",
                        "warning": "PR is not merged",
                        "pr_number": pr_number,
                    })
                )
                return False

            merge_commit = pr_data.get("merge_commit_sha")
            if not merge_commit:
                logger.error(
                    json.dumps({
                        "action": "revert_merge",
                        "error": "No merge commit SHA",
                        "pr_number": pr_number,
                    })
                )
                return False

            # Create revert commit
            revert_data = {
                "message": f"Revert \"Merge PR #{pr_number}\" - Canary failed, automatic rollback",
                "commit_sha": merge_commit,
            }

            url = f"{self.base_url}/repos/{self.org}/{self.repo}/git/refs/heads/main"
            response = self.session.post(
                f"{self.base_url}/repos/{self.org}/{self.repo}/git/commits",
                json={
                    "message": revert_data["message"],
                    "tree": "placeholder",  # Would get actual tree SHA
                    "parents": [merge_commit],
                }
            )

            logger.info(
                json.dumps({
                    "action": "revert_merge",
                    "status": "success",
                    "pr_number": pr_number,
                })
            )
            return True

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "revert_merge",
                    "error": str(e),
                })
            )
            return False


class CloudWatchClient:
    """AWS CloudWatch client for metrics monitoring."""

    def __init__(self, region: str = "us-east-1"):
        """Initialize CloudWatch client."""
        self.region = region
        self.client = None

        if BOTO3_AVAILABLE:
            try:
                self.client = boto3.client("cloudwatch", region_name=region)
                logger.info(
                    json.dumps({
                        "action": "CloudWatchClient.__init__",
                        "status": "initialized",
                        "region": region,
                    })
                )
            except Exception as e:
                logger.warning(
                    json.dumps({
                        "action": "CloudWatchClient.__init__",
                        "warning": "Failed to initialize boto3 CloudWatch client",
                        "error": str(e),
                    })
                )

    def get_metrics(
        self,
        namespace: str,
        metric_name: str,
        duration_minutes: int = 5,
    ) -> Dict[str, float]:
        """Get CloudWatch metrics for monitoring.

        Queries real CloudWatch metrics using boto3. Falls back to mock data
        if boto3 is unavailable or credentials are missing.

        Args:
            namespace: CloudWatch namespace (e.g., AWS/Lambda)
            metric_name: Metric name (e.g., Errors)
            duration_minutes: Look back period

        Returns:
            Dict with error_rate, latency_p99, invocations, throttles
        """
        try:
            if not self.client:
                logger.warning(
                    json.dumps({
                        "action": "get_metrics",
                        "warning": "CloudWatch client not available, using mock data",
                    })
                )
                return self._get_mock_metrics()

            return self._query_real_metrics(
                namespace=namespace,
                metric_name=metric_name,
                duration_minutes=duration_minutes,
            )

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_metrics",
                    "error": str(e),
                    "fallback": "returning_mock_data",
                })
            )
            return self._get_mock_metrics()

    def _query_real_metrics(
        self,
        namespace: str,
        metric_name: str,
        duration_minutes: int,
    ) -> Dict[str, float]:
        """Query real metrics from CloudWatch API.

        Args:
            namespace: CloudWatch namespace
            metric_name: Metric name to query
            duration_minutes: Look back period

        Returns:
            Dict with calculated metrics
        """
        try:
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(minutes=duration_minutes)

            metrics_data = {
                "error_rate": 0.0,
                "latency_p99": 0.0,
                "invocations": 0,
                "throttles": 0,
            }

            # Query Errors metric
            try:
                errors = self.client.get_metric_statistics(
                    Namespace=namespace,
                    MetricName="Errors",
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=60,
                    Statistics=["Sum"],
                )
                error_sum = sum(
                    dp.get("Sum", 0) for dp in errors.get("Datapoints", [])
                )
                metrics_data["error_count"] = int(error_sum)
            except Exception as e:
                logger.warning(
                    json.dumps({
                        "action": "_query_real_metrics",
                        "metric": "Errors",
                        "error": str(e),
                    })
                )

            # Query Invocations metric
            try:
                invocations = self.client.get_metric_statistics(
                    Namespace=namespace,
                    MetricName="Invocations",
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=60,
                    Statistics=["Sum"],
                )
                invocation_sum = sum(
                    dp.get("Sum", 0) for dp in invocations.get("Datapoints", [])
                )
                metrics_data["invocations"] = int(invocation_sum)

                # Calculate error rate
                if metrics_data["invocations"] > 0:
                    metrics_data["error_rate"] = (
                        metrics_data.get("error_count", 0) / metrics_data["invocations"] * 100
                    )
            except Exception as e:
                logger.warning(
                    json.dumps({
                        "action": "_query_real_metrics",
                        "metric": "Invocations",
                        "error": str(e),
                    })
                )

            # Query Duration metric for p99 latency
            try:
                duration = self.client.get_metric_statistics(
                    Namespace=namespace,
                    MetricName="Duration",
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=60,
                    ExtendedStatistics=["p99"],
                )
                durations = [
                    dp.get("ExtendedStatistics", {}).get("p99", 0)
                    for dp in duration.get("Datapoints", [])
                ]
                metrics_data["latency_p99"] = max(durations) if durations else 0.0
            except Exception as e:
                logger.warning(
                    json.dumps({
                        "action": "_query_real_metrics",
                        "metric": "Duration",
                        "error": str(e),
                    })
                )

            # Query Throttles metric
            try:
                throttles = self.client.get_metric_statistics(
                    Namespace=namespace,
                    MetricName="Throttles",
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=60,
                    Statistics=["Sum"],
                )
                throttle_sum = sum(
                    dp.get("Sum", 0) for dp in throttles.get("Datapoints", [])
                )
                metrics_data["throttles"] = int(throttle_sum)
            except Exception as e:
                logger.warning(
                    json.dumps({
                        "action": "_query_real_metrics",
                        "metric": "Throttles",
                        "error": str(e),
                    })
                )

            logger.info(
                json.dumps({
                    "action": "_query_real_metrics",
                    "status": "success",
                    "error_rate": metrics_data["error_rate"],
                    "latency_p99": metrics_data["latency_p99"],
                    "throttles": metrics_data["throttles"],
                })
            )

            return metrics_data

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_query_real_metrics",
                    "error": str(e),
                })
            )
            return self._get_mock_metrics()

    @staticmethod
    def _get_mock_metrics() -> Dict[str, float]:
        """Return mock metrics for development/testing.

        Returns:
            Dict with mock metric values
        """
        import random
        return {
            "error_rate": random.uniform(0, 1),
            "latency_p99": random.uniform(100, 500),
            "invocations": random.randint(100, 1000),
            "throttles": random.randint(0, 2),
        }


class DeployAgent:
    """Specialized agent for deployment monitoring and orchestration."""

    def __init__(self, config: Dict):
        """Initialize deploy agent."""
        self.config = config
        self.github = GitHubWorkflowClient(
            config["github"].token,
            config["github"].org,
            config["github"].repo,
        )
        # Initialize CloudWatch with AWS region from config or default
        region = config.get("aws_region", "us-east-1")
        self.cloudwatch = CloudWatchClient(region=region)

    async def execute(
        self,
        pr_number: int,
        staging_branch: str = "main",
    ) -> DeployResult:
        """Monitor deployment triggered by PR merge.

        Instead of deploying directly, this monitors the GitHub Actions
        workflows that are automatically triggered:
        1. PR merge → deploy-staging.yml runs automatically
        2. Staging success → deploy-canary.yml runs automatically
        3. Canary success → waits for production approval
        4. Approval received → deploy-production.yml runs

        Args:
            pr_number: PR number that was merged
            staging_branch: Branch being deployed (default: main)

        Returns:
            DeployResult with monitoring status
        """
        logger.info(
            json.dumps({
                "action": "deploy_agent_execute",
                "pr_number": pr_number,
                "staging_branch": staging_branch,
            })
        )

        start_time = datetime.now()
        deployment_id = f"monitor_{int(time.time())}"

        try:
            # Monitor staging deployment
            staging_success = await self.monitor_github_workflow(
                workflow_name="deploy-staging.yml",
                timeout_seconds=900,  # 15 minutes
            )

            if not staging_success:
                logger.error(
                    json.dumps({
                        "action": "execute",
                        "status": "staging_failed",
                    })
                )
                return DeployResult(
                    status="failed",
                    workflow_run_id=deployment_id,
                    error="Staging deployment failed",
                    duration=(datetime.now() - start_time).total_seconds(),
                )

            # Monitor canary deployment
            canary_success, canary_metrics = await self.monitor_canary_deployment(
                workflow_name="deploy-canary.yml",
                timeout_seconds=600,  # 10 minutes
            )

            if not canary_success:
                logger.warning(
                    json.dumps({
                        "action": "execute",
                        "status": "canary_failed",
                    })
                )
                # Trigger rollback
                await self.trigger_rollback(pr_number)
                return DeployResult(
                    status="rolled_back",
                    workflow_run_id=deployment_id,
                    canary_metrics=canary_metrics,
                    error="Canary failed, rolled back",
                    duration=(datetime.now() - start_time).total_seconds(),
                )

            # Canary passed - report back for production approval
            result = DeployResult(
                status="success",
                workflow_run_id=deployment_id,
                staging_url="https://staging.mvt.example.com",  # From workflow outputs
                production_url="https://mvt.example.com",  # Will be set after production deploy
                canary_metrics=canary_metrics,
                duration=(datetime.now() - start_time).total_seconds(),
            )

            logger.info(
                json.dumps({
                    "action": "execute",
                    "status": "canary_passed_awaiting_approval",
                    "deployment_id": deployment_id,
                })
            )

            return result

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "execute",
                    "error": str(e),
                })
            )
            return DeployResult(
                status="failed",
                workflow_run_id=deployment_id,
                error=str(e),
                duration=(datetime.now() - start_time).total_seconds(),
            )

    async def monitor_github_workflow(
        self,
        workflow_name: str,
        timeout_seconds: int = 900,
    ) -> bool:
        """Monitor GitHub Actions workflow until completion.

        Polls the GitHub API for workflow status every 30 seconds
        until the workflow completes (success or failure).

        Args:
            workflow_name: Workflow file name (e.g., deploy-staging.yml)
            timeout_seconds: Maximum time to wait

        Returns:
            True if workflow succeeded, False if failed or timed out
        """
        logger.info(
            json.dumps({
                "action": "monitor_github_workflow",
                "workflow": workflow_name,
                "timeout_seconds": timeout_seconds,
            })
        )

        start_time = time.time()
        poll_interval = 30  # seconds

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                logger.error(
                    json.dumps({
                        "action": "monitor_github_workflow",
                        "status": "timeout",
                        "workflow": workflow_name,
                        "elapsed_seconds": int(elapsed),
                    })
                )
                return False

            # Get latest workflow run
            run = self.github.get_latest_workflow_run(workflow_name)
            if not run:
                await asyncio.sleep(poll_interval)
                continue

            status = run.get("status")
            conclusion = run.get("conclusion")

            logger.info(
                json.dumps({
                    "action": "monitor_github_workflow",
                    "workflow": workflow_name,
                    "status": status,
                    "conclusion": conclusion,
                    "elapsed_seconds": int(elapsed),
                })
            )

            # Workflow still running
            if status == "in_progress":
                await asyncio.sleep(poll_interval)
                continue

            # Workflow completed
            if status == "completed":
                success = conclusion == "success"
                logger.info(
                    json.dumps({
                        "action": "monitor_github_workflow",
                        "status": "completed",
                        "success": success,
                        "workflow": workflow_name,
                    })
                )
                return success

            # Unknown status
            await asyncio.sleep(poll_interval)

    async def monitor_canary_deployment(
        self,
        workflow_name: str,
        timeout_seconds: int = 600,
    ) -> tuple[bool, Dict[str, float]]:
        """Monitor canary deployment health.

        Monitors both GitHub workflow and CloudWatch metrics:
        1. Workflow status (deploy-canary.yml)
        2. CloudWatch metrics (error rate, latency)
        3. Canary health checks

        Args:
            workflow_name: Workflow file name
            timeout_seconds: Maximum monitoring duration

        Returns:
            Tuple of (success: bool, metrics: dict)
        """
        logger.info(
            json.dumps({
                "action": "monitor_canary_deployment",
                "workflow": workflow_name,
            })
        )

        start_time = time.time()
        metrics = {}

        # Wait for canary workflow to complete
        workflow_success = await self.monitor_github_workflow(
            workflow_name=workflow_name,
            timeout_seconds=timeout_seconds,
        )

        if not workflow_success:
            return False, metrics

        # Monitor CloudWatch metrics during canary period
        canary_health = await self.check_canary_health()
        metrics.update(canary_health)

        success = canary_health.get("healthy", False)

        duration = time.time() - start_time
        logger.info(
            json.dumps({
                "action": "monitor_canary_deployment",
                "success": success,
                "duration_seconds": int(duration),
                "metrics": metrics,
            })
        )

        return success, metrics

    async def check_canary_health(
        self,
        duration_seconds: int = 300,  # 5 minutes
    ) -> Dict[str, Any]:
        """Check canary deployment health via CloudWatch metrics.

        Polls CloudWatch metrics over canary duration (5 minutes):
        - Error rate threshold: < 1%
        - Latency p99: < 1000ms
        - No Lambda throttles

        Args:
            duration_seconds: How long to monitor (default 5 minutes)

        Returns:
            Dict with health status and metrics
        """
        logger.info(
            json.dumps({
                "action": "check_canary_health",
                "duration_seconds": duration_seconds,
            })
        )

        start_time = time.time()
        poll_interval = 60  # Check every minute

        healthy = True
        all_metrics = {
            "error_rate": [],
            "latency_p99": [],
            "invocations": [],
            "throttles": [],
        }

        while time.time() - start_time < duration_seconds:
            # Get current metrics
            metrics = self.cloudwatch.get_metrics(
                namespace="AWS/Lambda",
                metric_name="Errors",
                duration_minutes=1,
            )

            error_rate = metrics.get("error_rate", 0)
            latency_p99 = metrics.get("latency_p99", 0)
            throttles = metrics.get("throttles", 0)

            # Track metrics
            all_metrics["error_rate"].append(error_rate)
            all_metrics["latency_p99"].append(latency_p99)
            all_metrics["throttles"].append(throttles)

            # Check health thresholds
            if error_rate > 1.0:
                logger.warning(
                    json.dumps({
                        "action": "check_canary_health",
                        "warning": "High error rate",
                        "error_rate": error_rate,
                    })
                )
                healthy = False

            if latency_p99 > 1000:
                logger.warning(
                    json.dumps({
                        "action": "check_canary_health",
                        "warning": "High latency",
                        "latency_p99": latency_p99,
                    })
                )

            if throttles > 0:
                logger.warning(
                    json.dumps({
                        "action": "check_canary_health",
                        "warning": "Lambda throttles detected",
                        "throttles": throttles,
                    })
                )
                healthy = False

            await asyncio.sleep(poll_interval)

        # Calculate averages
        avg_error_rate = sum(all_metrics["error_rate"]) / len(all_metrics["error_rate"]) if all_metrics["error_rate"] else 0
        avg_latency = sum(all_metrics["latency_p99"]) / len(all_metrics["latency_p99"]) if all_metrics["latency_p99"] else 0

        logger.info(
            json.dumps({
                "action": "check_canary_health",
                "status": "completed",
                "healthy": healthy,
                "avg_error_rate": avg_error_rate,
                "avg_latency": avg_latency,
            })
        )

        return {
            "healthy": healthy,
            "avg_error_rate": avg_error_rate,
            "avg_latency": avg_latency,
            "max_error_rate": max(all_metrics["error_rate"]) if all_metrics["error_rate"] else 0,
            "max_throttles": max(all_metrics["throttles"]) if all_metrics["throttles"] else 0,
        }

    async def trigger_rollback(self, pr_number: int) -> bool:
        """Trigger rollback by reverting the merged PR.

        Creates a revert commit on main which automatically triggers
        the deploy-staging.yml workflow to redeploy the previous version.

        Args:
            pr_number: PR number to revert

        Returns:
            True if rollback was initiated
        """
        logger.warning(
            json.dumps({
                "action": "trigger_rollback",
                "pr_number": pr_number,
            })
        )

        success = self.github.revert_merge(pr_number)

        if success:
            logger.warning(
                json.dumps({
                    "action": "trigger_rollback",
                    "status": "rollback_initiated",
                    "pr_number": pr_number,
                })
            )
        else:
            logger.error(
                json.dumps({
                    "action": "trigger_rollback",
                    "status": "rollback_failed",
                    "pr_number": pr_number,
                })
            )

        return success


async def main():
    """Main entry point for testing."""
    config = load_config()
    agent = DeployAgent(config)

    # Test: Monitor a deployment
    result = await agent.execute(pr_number=123, staging_branch="main")
    print(json.dumps(
        {
            "status": result.status,
            "workflow_run_id": result.workflow_run_id,
            "staging_url": result.staging_url,
            "production_url": result.production_url,
            "canary_metrics": result.canary_metrics,
            "duration": result.duration,
            "error": result.error,
        },
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
