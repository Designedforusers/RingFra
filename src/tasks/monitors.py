"""
Service health monitoring.

Polls Render services and checks for:
- Service status (up/down)
- High CPU/memory
- Failed deploys
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
from loguru import logger

from src.config import settings


class HealthStatus(str, Enum):
    """Service health status."""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class ServiceHealth:
    """Health status for a single service."""

    name: str
    service_id: str
    status: HealthStatus
    message: str
    cpu_percent: float | None = None
    memory_percent: float | None = None


@dataclass
class HealthReport:
    """Overall health report for all services."""

    services: list[ServiceHealth]
    timestamp: str

    @property
    def severity(self) -> str:
        """Get the highest severity in the report."""
        if any(s.status == HealthStatus.CRITICAL for s in self.services):
            return "critical"
        if any(s.status == HealthStatus.WARNING for s in self.services):
            return "warning"
        return "healthy"

    @property
    def summary(self) -> str:
        """Get a brief summary of the health report."""
        total = len(self.services)
        critical = sum(1 for s in self.services if s.status == HealthStatus.CRITICAL)
        warning = sum(1 for s in self.services if s.status == HealthStatus.WARNING)
        healthy = total - critical - warning

        if critical > 0:
            issues = [s.name for s in self.services if s.status == HealthStatus.CRITICAL]
            return f"{critical} critical issue(s): {', '.join(issues)}"
        if warning > 0:
            issues = [s.name for s in self.services if s.status == HealthStatus.WARNING]
            return f"{warning} warning(s): {', '.join(issues)}"
        return f"All {total} services healthy"

    @property
    def issues(self) -> list[ServiceHealth]:
        """Get all services with issues."""
        return [s for s in self.services if s.status != HealthStatus.HEALTHY]


async def render_api(method: str, endpoint: str, data: dict | None = None) -> dict:
    """Make a request to the Render API."""
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=f"https://api.render.com/v1{endpoint}",
            headers={
                "Authorization": f"Bearer {settings.RENDER_API_KEY}",
                "Content-Type": "application/json",
            },
            json=data,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json() if response.text else {}


async def get_all_services() -> list[dict[str, Any]]:
    """Get all services from Render API."""
    try:
        data = await render_api("GET", "/services")
        services = data if isinstance(data, list) else data.get("services", data.get("items", []))
        return [svc.get("service", svc) for svc in services]
    except Exception as e:
        logger.error(f"Error fetching services: {e}")
        return []


async def get_service_metrics(service_id: str) -> dict[str, float]:
    """Get current metrics for a service."""
    try:
        # Use the Render metrics endpoint
        data = await render_api("GET", f"/services/{service_id}/metrics")

        # Extract CPU and memory if available
        metrics = {}
        if "cpu" in data:
            metrics["cpu"] = data["cpu"].get("percent", 0)
        if "memory" in data:
            metrics["memory"] = data["memory"].get("percent", 0)

        return metrics
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # Metrics not available for this service type
            return {}
        logger.error(f"Error fetching metrics for {service_id}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error fetching metrics for {service_id}: {e}")
        return {}


async def get_latest_deploy_status(service_id: str) -> dict[str, Any]:
    """Get the latest deploy status for a service."""
    try:
        data = await render_api("GET", f"/services/{service_id}/deploys?limit=1")
        deploys = data if isinstance(data, list) else data.get("deploys", data.get("items", []))

        if deploys:
            deploy = deploys[0].get("deploy", deploys[0])
            return {
                "status": deploy.get("status"),
                "created_at": deploy.get("createdAt"),
                "finished_at": deploy.get("finishedAt"),
            }
        return {}
    except Exception as e:
        logger.error(f"Error fetching deploy status for {service_id}: {e}")
        return {}


async def check_service_health(service: dict[str, Any]) -> ServiceHealth:
    """Check the health of a single service."""
    name = service.get("name", "unknown")
    service_id = service.get("id", "")
    service_type = service.get("type", "")
    suspended = service.get("suspended", "not_suspended")

    # Check if suspended
    if suspended == "suspended":
        return ServiceHealth(
            name=name,
            service_id=service_id,
            status=HealthStatus.WARNING,
            message="Service is suspended",
        )

    # Get metrics if available
    metrics = await get_service_metrics(service_id)
    cpu = metrics.get("cpu")
    memory = metrics.get("memory")

    # Check for critical CPU/memory
    if cpu is not None and cpu > 95:
        return ServiceHealth(
            name=name,
            service_id=service_id,
            status=HealthStatus.CRITICAL,
            message=f"CPU at {cpu:.0f}%",
            cpu_percent=cpu,
            memory_percent=memory,
        )

    if memory is not None and memory > 95:
        return ServiceHealth(
            name=name,
            service_id=service_id,
            status=HealthStatus.CRITICAL,
            message=f"Memory at {memory:.0f}%",
            cpu_percent=cpu,
            memory_percent=memory,
        )

    # Check for warning levels
    if cpu is not None and cpu > 80:
        return ServiceHealth(
            name=name,
            service_id=service_id,
            status=HealthStatus.WARNING,
            message=f"High CPU: {cpu:.0f}%",
            cpu_percent=cpu,
            memory_percent=memory,
        )

    if memory is not None and memory > 80:
        return ServiceHealth(
            name=name,
            service_id=service_id,
            status=HealthStatus.WARNING,
            message=f"High memory: {memory:.0f}%",
            cpu_percent=cpu,
            memory_percent=memory,
        )

    # Check latest deploy status
    deploy = await get_latest_deploy_status(service_id)
    if deploy.get("status") == "build_failed":
        return ServiceHealth(
            name=name,
            service_id=service_id,
            status=HealthStatus.CRITICAL,
            message="Latest deploy failed",
            cpu_percent=cpu,
            memory_percent=memory,
        )

    if deploy.get("status") == "update_failed":
        return ServiceHealth(
            name=name,
            service_id=service_id,
            status=HealthStatus.CRITICAL,
            message="Service update failed",
            cpu_percent=cpu,
            memory_percent=memory,
        )

    return ServiceHealth(
        name=name,
        service_id=service_id,
        status=HealthStatus.HEALTHY,
        message="Healthy",
        cpu_percent=cpu,
        memory_percent=memory,
    )


async def poll_render_services() -> HealthReport:
    """
    Poll all Render services and return a health report.

    This is the main function called by the ARQ worker.
    """
    from datetime import datetime, timezone

    logger.info("Polling Render services for health check")

    services = await get_all_services()
    health_checks = []

    for service in services:
        health = await check_service_health(service)
        health_checks.append(health)
        if health.status != HealthStatus.HEALTHY:
            logger.warning(f"Service {health.name}: {health.status.value} - {health.message}")

    report = HealthReport(
        services=health_checks,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(f"Health check complete: {report.summary}")
    return report


async def check_specific_service(service_name: str) -> ServiceHealth | None:
    """Check health of a specific service by name."""
    services = await get_all_services()

    for service in services:
        if service.get("name", "").lower() == service_name.lower():
            return await check_service_health(service)

    return None
