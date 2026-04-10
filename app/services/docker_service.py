import asyncio
import shutil
import tempfile
import socket
from functools import partial
from typing import Dict, Any, Optional

import docker
from docker.errors import ImageNotFound, APIError, NotFound, BuildError


# Custom exceptions

class DockerServiceError(Exception):
    """Base exception for all Docker service-related errors."""


class DockerImageError(DockerServiceError):
    """Raised when a Docker image cannot be found or pulled."""


class ContainerStartError(DockerServiceError):
    """Raised when a container fails to start for any reason."""


class GitCloneError(DockerServiceError):
    """Raised when a 'git clone' subprocess returns a non-zero exit code."""


# Service

class DockerService:
    """Manages the lifecycle of Docker containers asynchronously.

    Must be used as an async context manager so that the Docker client is
    initialised and closed properly without blocking the event loop:

        async with DockerService() as svc:
            result = await svc.run_container(...)
    """

    def __init__(self) -> None:
        self.client = None

    async def __aenter__(self) -> "DockerService":
        loop = asyncio.get_running_loop()
        try:
            self.client = await loop.run_in_executor(None, docker.from_env)
            await loop.run_in_executor(None, self.client.ping)
        except Exception as exc:
            raise ConnectionError(
                f"Could not connect to the Docker daemon. Is it running? Error: {exc}"
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.client:
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.client.close)
            except Exception:
                pass

    # Internal sync helpers (run inside executor threads)

    def _run_container_sync(
        self,
        image_tag: str,
        internal_port: int,
        environment: Optional[Dict[str, str]],
        cpu_limit: Optional[str],
        mem_limit: Optional[str] = None,
        network: Optional[str] = None,
        name: Optional[str] = None,
        volumes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Pull (if needed) and start a container. Synchronous — call via run_in_executor.

        Port allocation strategy
        ------------------------
        We use a **dynamic collision-free mapping**. 

        1. Before starting the container, we find an available random port on the 
           host using ``_find_free_port_sync()``.
        2. We use this as the ``host_port``.
        3. This ensures that common ports like 5432 (Postgres) or 80 won't 
           collide across different deployments.

        ${PORT} and ${HOST_PORT} injection
        ---------------------------------
        - ``${PORT}`` is replaced with ``internal_port`` (the port the app 
          listens on INSIDE the container).
        - ``${HOST_PORT}`` is replaced with the assigned host port (the port 
          the BROWSER or external tools use).

        For Next.js redirects to work, use e.g. ``AUTH_URL=http://host:${HOST_PORT}``.
        """

        # 1. Ensure the image is available locally
        try:
            self.client.images.get(image_tag)
            print(f"[DockerService] Image '{image_tag}' exists locally.")
        except NotFound:
            try:
                print(f"[DockerService] Pulling image: {image_tag} …")
                self.client.images.pull(image_tag)
            except ImageNotFound:
                raise DockerImageError(f"Image '{image_tag}' not found.")
            except APIError as exc:
                raise DockerImageError(f"Failed to pull image '{image_tag}': {exc}")
        except APIError as exc:
            raise DockerServiceError(f"Docker API error when checking image '{image_tag}': {exc}")


        # 3. Port Discovery
        host_port = self._find_free_port_sync() if internal_port else None
        port_mapping = {f"{internal_port}/tcp": host_port} if internal_port else {}

        # 4. Build env — ${PORT} (internal) and ${HOST_PORT} (external)
        processed_env: Dict[str, str] = {}
        if environment:
            for k, v in environment.items():
                if isinstance(v, str):
                    val = v.replace("${PORT}", str(internal_port))
                    if host_port:
                        val = val.replace("${HOST_PORT}", str(host_port))
                    processed_env[k] = val
                else:
                    processed_env[k] = v

        nano_cpus = int(float(cpu_limit) * 1_000_000_000) if cpu_limit else None

        kwargs: Dict[str, Any] = dict(
            image=image_tag,
            detach=True,
            ports=port_mapping,
            environment=processed_env,
        )
        if mem_limit:
            kwargs["mem_limit"] = mem_limit
        if volumes:
            kwargs["volumes"] = {
                host_path: {"bind": container_path, "mode": "rw"}
                for host_path, container_path in volumes.items()
            }
        if nano_cpus:
            kwargs["nano_cpus"] = nano_cpus
        if network:
            kwargs["network"] = network
            try:
                self.client.networks.get(network)
            except NotFound:
                try:
                    print(f"[DockerService] Creating network '{network}' …")
                    self.client.networks.create(network, driver="bridge")
                except APIError as api_exc:
                    # Another parallel deploy may have created it already, or we've hit IP constraints.
                    # Verify if it actually exists.
                    try:
                        self.client.networks.get(network)
                    except NotFound:
                        raise DockerServiceError(f"Critical failure creating network '{network}': {api_exc}")
        if name:
            kwargs["name"] = name

      
        # 4. Start the container
  
        try:
            print(f"[DockerService] Starting container '{name or image_tag}' …")
            container = self.client.containers.run(**kwargs)
        except APIError as exc:
            raise ContainerStartError(f"Failed to start container for image '{image_tag}': {exc}")

        
        # 5. Reload attrs to discover the host port Docker actually bound
      
        try:
            container.reload()
            port_key = f"{internal_port}/tcp"
            port_bindings = container.attrs.get("NetworkSettings", {}).get("Ports", {})
            bindings = port_bindings.get(port_key)
            if not bindings:
                raise ContainerStartError(
                    f"Container {container.id[:12]} started but no host port was bound for "
                    f"{port_key}. NetworkSettings.Ports={port_bindings}"
                )
            host_port = int(bindings[0]["HostPort"])
            
            # Extract internal IP address for healthchecks
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            # If a specific network was requested, try to get IP from it, otherwise take first.
            if network and network in networks:
                internal_ip = networks[network].get("IPAddress")
            else:
                internal_ip = next(iter(networks.values())).get("IPAddress") if networks else None

        except APIError as exc:
            raise ContainerStartError(
                f"Failed to reload container {container.id[:12]} after start: {exc}"
            )

        print(
            f"[DockerService] Container {container.id[:12]} started — "
            f"host_port={host_port}, internal_ip={internal_ip}, internal_port={internal_port}."
        )
        return {"container_id": container.id, "port": host_port, "ip": internal_ip}

    def _build_image_sync(self, path: str, tag: str) -> str:
        """Build a Docker image from a local Dockerfile. Synchronous — call via run_in_executor."""
        print(f"[DockerService] Building image '{tag}' from {path} …")
        logs_output = []
        try:
            _image, build_logs = self.client.images.build(path=path, tag=tag, rm=True)
            for chunk in build_logs:
                if "stream" in chunk:
                    line = chunk["stream"]
                    logs_output.append(line)
                    print(line, end="")
            return "".join(logs_output)
        except BuildError as exc:
            for chunk in exc.build_log:
                if "stream" in chunk:
                    logs_output.append(chunk["stream"])
            msg = f"Failed to build image from '{path}': {exc}\n" + "".join(logs_output)
            raise ContainerStartError(msg)
        except Exception as exc:
            raise ContainerStartError(f"Failed to build image from '{path}': {exc}\n" + "".join(logs_output))

    def _stop_container_sync(self, container_id: str) -> None:
        """Stop a container by ID. Synchronous — call via run_in_executor."""
        try:
            print(f"[DockerService] Stopping container {container_id[:12]} …")
            container = self.client.containers.get(container_id)
            container.stop(timeout=10)
            print(f"[DockerService] Container {container_id[:12]} stopped.")
        except NotFound:
            print(f"[DockerService] Container {container_id[:12]} already gone.")
        except APIError as exc:
            raise DockerServiceError(f"API error stopping container {container_id[:12]}: {exc}")

    def _start_container_sync(self, container_id: str) -> None:
        """Start a stopped container by ID. Synchronous."""
        try:
            print(f"[DockerService] Starting stopped container {container_id[:12]} …")
            container = self.client.containers.get(container_id)
            container.start()
            print(f"[DockerService] Container {container_id[:12]} started.")
        except NotFound:
            raise DockerServiceError(f"Container {container_id[:12]} not found.")
        except APIError as exc:
            raise DockerServiceError(f"API error starting container {container_id[:12]}: {exc}")

    def _remove_container_sync(self, container_id: str) -> None:
        """Remove a container by ID (forces removal). Synchronous."""
        try:
            print(f"[DockerService] Removing container {container_id[:12]} …")
            container = self.client.containers.get(container_id)
            container.remove(force=True)
            print(f"[DockerService] Container {container_id[:12]} removed.")
        except NotFound:
            print(f"[DockerService] Container {container_id[:12]} already gone.")
        except APIError as exc:
            raise DockerServiceError(f"API error removing container {container_id[:12]}: {exc}")

    def _get_container_logs_sync(self, container_id: str, tail: int = 100) -> str:
        """Retrieve the last N log lines from a container. Synchronous."""
        try:
            container = self.client.containers.get(container_id)
            return container.logs(tail=tail).decode("utf-8")
        except NotFound:
            raise DockerServiceError(f"Container {container_id} not found.")
        except APIError as exc:
            raise DockerServiceError(f"Failed to get logs for container {container_id}: {exc}")

    def _create_volume_sync(self, name: str) -> None:
        """Create a new Docker volume. Synchronous."""
        try:
            self.client.volumes.create(name=name)
        except APIError as exc:
            raise DockerServiceError(f"Failed to create volume {name}: {exc}")

    def _remove_volume_sync(self, name: str) -> None:
        """Remove a Docker volume. Synchronous."""
        try:
            volume = self.client.volumes.get(name)
            volume.remove(force=True)
        except NotFound:
            pass # Already gone
        except APIError as exc:
            if "in use" in str(exc).lower():
                raise DockerServiceError(f"Volume '{name}' is currently in use by a container.")
            raise DockerServiceError(f"Failed to remove volume {name}: {exc}")

    def _remove_network_sync(self, name: str) -> None:
        """Remove a Docker network. Synchronous."""
        try:
            network = self.client.networks.get(name)
            network.remove()
            print(f"[DockerService] Network '{name}' removed.")
        except NotFound:
            pass # Already gone
        except APIError as exc:
            if "has active endpoints" in str(exc).lower():
                raise DockerServiceError(f"Network '{name}' currently has active endpoints.")
            raise DockerServiceError(f"Failed to remove network {name}: {exc}")

    def _find_free_port_sync(self) -> int:
        """Find an available port on the host. Synchronous."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            port = s.getsockname()[1]
            return port


    # Public async API


    def _assert_client(self) -> None:
        if not self.client:
            raise RuntimeError("DockerService must be used as an async context manager.")

    async def run_container(
        self,
        image_tag: str,
        internal_port: int,
        environment: Optional[Dict[str, str]] = None,
        cpu_limit: Optional[str] = None,
        mem_limit: Optional[str] = None,
        network: Optional[str] = None,
        name: Optional[str] = None,
        volumes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Pull an image and run a container asynchronously."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self._run_container_sync, image_tag, internal_port, environment, cpu_limit, mem_limit, network, name, volumes),
        )

    async def create_volume(self, name: str) -> None:
        """Create a standalone Docker volume asynchronously."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._create_volume_sync, name))

    async def remove_volume(self, name: str) -> None:
        """Remove a standalone Docker volume asynchronously."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._remove_volume_sync, name))

    async def remove_network(self, network_name: str) -> None:
        """Remove a Docker network asynchronously."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._remove_network_sync, network_name))

    async def clone_repo(self, git_url: str) -> str:
        """Asynchronously clone a repo to a temporary directory. Returns the path."""
        self._assert_client()
        tmp_dir = tempfile.mkdtemp(prefix="envondemand_git_")
        print(f"[DockerService] Cloning {git_url} into {tmp_dir} …")
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", git_url, tmp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            raise GitCloneError(f"git clone failed (exit {proc.returncode}) for '{git_url}': {err_msg}")
        return tmp_dir

    async def build_image(self, tmp_dir: str, image_tag: str) -> str:
        """Asynchronously build a Docker image. Returns build logs."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self._build_image_sync, tmp_dir, image_tag),
        )

    def cleanup_repo(self, tmp_dir: str) -> None:
        """Clean up the temporary repo."""
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"[DockerService] Cleaned up temp dir {tmp_dir}.")

    async def stop_container(self, container_id: str) -> None:
        """Asynchronously stop a container."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._stop_container_sync, container_id))

    async def start_container(self, container_id: str) -> None:
        """Asynchronously start a stopped container."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._start_container_sync, container_id))

    async def remove_container(self, container_id: str) -> None:
        """Asynchronously remove a container."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._remove_container_sync, container_id))

    async def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        """Asynchronously retrieve the last N log lines of a container."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._get_container_logs_sync, container_id, tail)
        )
