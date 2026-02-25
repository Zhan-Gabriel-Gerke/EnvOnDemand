import socket
import asyncio
from typing import Dict, Any, Optional
from functools import partial

import docker
from docker.errors import ImageNotFound, APIError, NotFound


class DockerServiceError(Exception):
    """Base exception for all Docker service-related errors."""
    pass


class DockerImageError(DockerServiceError):
    """Raised when a Docker image cannot be found or pulled."""
    pass


class ContainerStartError(DockerServiceError):
    """Raised when a container fails to start for any reason."""
    pass


class DockerService:
    """A service class to manage the lifecycle of Docker containers asynchronously."""

    def __init__(self):
        """
        Initializes the DockerService.
        Actual client initialization happens in __aenter__ or lazy load to avoid blocking __init__.
        """
        self.client = None

    async def __aenter__(self):
        """
        Async context manager entry. Initializes the Docker client.
        Raises ConnectionError if it cannot connect to the Docker daemon.
        """
        loop = asyncio.get_running_loop()
        try:
            # Running client creation in executor because it might check the socket
            self.client = await loop.run_in_executor(None, docker.from_env)
            await loop.run_in_executor(None, self.client.ping)
        except Exception as e:
            raise ConnectionError(f"Could not connect to the Docker daemon. Is it running? Error: {e}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Async context manager exit. Closes the Docker client.
        """
        if self.client:
            # client.close() is synchronous but fast, usually safe to run directly,
            # but for strict correctness effectively we can run it in executor too.
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.client.close)
            except Exception:
                pass  # Swallow errors on close

    def _find_free_port_sync(self) -> int:
        """
        Finds and returns a free port on the host machine.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return s.getsockname()[1]

    def _run_container_sync(
        self,
        image_tag: str,
        internal_port: int,
        environment: Optional[Dict[str, str]],
        cpu_limit: Optional[str],
    ) -> Dict[str, Any]:
        """
        Synchronous implementation of run_container.
        """
        try:
            print(f"Pulling image: {image_tag}...")
            self.client.images.pull(image_tag)
        except ImageNotFound:
            raise DockerImageError(f"Image '{image_tag}' not found.")

        free_port = self._find_free_port_sync()
        if not free_port:
            raise ContainerStartError("Could not find a free port on the host.")

        port_mapping = {f"{internal_port}/tcp": free_port}
        nano_cpus = int(float(cpu_limit) * 1_000_000_000) if cpu_limit else None

        try:
            print(f"Starting container for image {image_tag} on port {free_port}...")
            container = self.client.containers.run(
                image=image_tag,
                detach=True,
                ports=port_mapping,
                environment=environment,
                nano_cpus=nano_cpus,
            )
            print(f"Container {container.id[:12]} started successfully.")
            return {"container_id": container.id, "port": free_port}
        except APIError as e:
            raise ContainerStartError(f"Failed to start container for image '{image_tag}': {e}")

    async def run_container(
        self,
        image_tag: str,
        internal_port: int,
        environment: Optional[Dict[str, str]] = None,
        cpu_limit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Asynchronously runs a container.
        """
        if not self.client:
            raise RuntimeError("DockerService must be used as an async context manager.")
        
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(
                self._run_container_sync,
                image_tag,
                internal_port,
                environment,
                cpu_limit
            )
        )

    def _stop_container_sync(self, container_id: str) -> None:
        """
        Synchronous implementation of stop_container.
        """
        try:
            print(f"Attempting to stop and remove container {container_id[:12]}...")
            container = self.client.containers.get(container_id)
            container.remove(force=True)
            print(f"Container {container_id[:12]} stopped and removed successfully.")
        except NotFound:
            print(f"Container {container_id[:12]} not found. It might have been already removed.")
            pass
        except APIError as e:
            raise DockerServiceError(f"API error removing container {container_id[:12]}: {e}")

    async def stop_container(self, container_id: str) -> None:
        """
        Asynchronously stops a container.
        """
        if not self.client:
             raise RuntimeError("DockerService must be used as an async context manager.")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._stop_container_sync, container_id))

    def _get_container_logs_sync(self, container_id: str, tail: int = 100) -> str:
        """
        Synchronous implementation of get_container_logs.
        """
        try:
            container = self.client.containers.get(container_id)
            # logs returns bytes, so we decode it
            return container.logs(tail=tail).decode("utf-8")
        except NotFound:
            raise DockerServiceError(f"Container {container_id} not found.")
        except APIError as e:
            raise DockerServiceError(f"Failed to get logs for container {container_id}: {e}")

    async def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        """
        Asynchronously retrieves logs from a container.
        """
        if not self.client:
            raise RuntimeError("DockerService must be used as an async context manager.")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._get_container_logs_sync, container_id, tail))
