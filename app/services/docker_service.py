import socket
from typing import Dict, Any, Optional

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
    """A service class to manage the lifecycle of Docker containers."""

    def __init__(self):
        """
        Initializes the Docker client from environment variables.
        Raises ConnectionError if it cannot connect to the Docker daemon.
        """
        try:
            self.client = docker.from_env()
            self.client.ping()
        except Exception:
            raise ConnectionError("Could not connect to the Docker daemon. Is it running?")

    def _find_free_port(self) -> int:
        """
        Finds and returns a free port on the host machine.
        This is done by binding a temporary socket to port 0 and checking the assigned port.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return s.getsockname()[1]

    def run_container(
        self,
        image_tag: str,
        internal_port: int,
        environment: Optional[Dict[str, str]] = None,
        cpu_limit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Pulls an image, finds a free host port, and runs a new container.

        Args:
            image_tag: The tag of the Docker image to run (e.g., 'postgres:15-alpine').
            internal_port: The port inside the container to expose.
            environment: A dictionary of environment variables to set in the container.
            cpu_limit: A string specifying the CPU limit (e.g., "1.5").

        Returns:
            A dictionary containing the new container's ID and the external port it's mapped to.

        Raises:
            DockerImageError: If the specified image cannot be found or pulled.
            ContainerStartError: If the container fails to start.
        """
        try:
            print(f"Pulling image: {image_tag}...")
            self.client.images.pull(image_tag)
        except ImageNotFound:
            raise DockerImageError(f"Image '{image_tag}' not found.")

        free_port = self._find_free_port()
        if not free_port:
            raise ContainerStartError("Could not find a free port on the host.")

        port_mapping = {f"{internal_port}/tcp": free_port}
        
        nano_cpus = int(float(cpu_limit) * 1_000_000_000) if cpu_limit else None

        try:
            print(f"Starting container for image {image_tag} on port {free_port}...")
            container = self.client.containers.run(
                image=image_tag,
                detach=True,  # Run in the background
                ports=port_mapping,
                environment=environment,
                nano_cpus=nano_cpus,
            )
            print(f"Container {container.id[:12]} started successfully.")
            return {"container_id": container.id, "port": free_port}
        except APIError as e:
            raise ContainerStartError(f"Failed to start container for image '{image_tag}': {e}")

    def stop_container(self, container_id: str) -> None:
        """
        Stops and removes a container by its ID.

        This operation is idempotent: if the container doesn't exist, it does nothing.

        Args:
            container_id: The ID of the container to stop and remove.
        
        Raises:
            DockerServiceError: If an error occurs during container removal.
        """
        try:
            print(f"Attempting to stop and remove container {container_id[:12]}...")
            container = self.client.containers.get(container_id)
            # Using force=True is equivalent to `docker stop` followed by `docker rm`.
            container.remove(force=True)
            print(f"Container {container_id[:12]} stopped and removed successfully.")
        except NotFound:
            # If the container is already gone, we consider the operation successful.
            print(f"Container {container_id[:12]} not found. It might have been already removed.")
            pass
        except APIError as e:
            raise DockerServiceError(f"API error removing container {container_id[:12]}: {e}")
