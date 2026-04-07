import asyncio
import shutil
import tempfile
from functools import partial
from typing import Dict, Any, Optional

import docker
from docker.errors import ImageNotFound, APIError, NotFound


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
        network: Optional[str] = None,
        name: Optional[str] = None,
        volumes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Pull (if needed) and start a container. Synchronous — call via run_in_executor.

        Port allocation strategy
        ------------------------
        We pass ``{internal_port}/tcp: None`` to the Docker daemon so that it
        picks a free ephemeral host port automatically.  This completely
        eliminates the TOCTOU race condition that existed when we called
        _find_free_port_sync() ourselves (bind → close → Docker binds — three
        steps, not atomic).

        After the container is started we call ``container.reload()`` to fetch
        the freshly-assigned host port from
        ``container.attrs['NetworkSettings']['Ports']``.

        ${PORT} injection
        -----------------
        The ``${PORT}`` placeholder in environment values is replaced with
        ``internal_port`` — the port the application *listens on inside the
        container*.  This mirrors the convention used by Railway, Render and
        Fly.io: the app binds to $PORT internally; the platform decides which
        host port forwards to it.  The host port is returned in the result dict
        so callers can record it (e.g. in the database) and expose it to users.
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


        # 2. Build env — ${PORT} → internal_port (app's own listen port)

        processed_env: Dict[str, str] = {}
        if environment:
            for k, v in environment.items():
                if isinstance(v, str):
                    processed_env[k] = v.replace("${PORT}", str(internal_port))
                else:
                    processed_env[k] = v

      
        # 3. Build run kwargs
        #    ports={"<internal>/tcp": None}  → Docker auto-assigns host port
      
        port_mapping = {f"{internal_port}/tcp": None} if internal_port else {}
        nano_cpus = int(float(cpu_limit) * 1_000_000_000) if cpu_limit else None

        kwargs: Dict[str, Any] = dict(
            image=image_tag,
            detach=True,
            ports=port_mapping,
            environment=processed_env,
        )
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
                except APIError:
                    # Another parallel deploy may have created it already — safe to ignore.
                    pass
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
        except APIError as exc:
            raise ContainerStartError(
                f"Failed to reload container {container.id[:12]} after start: {exc}"
            )

        print(
            f"[DockerService] Container {container.id[:12]} started — "
            f"host_port={host_port}, internal_port={internal_port}."
        )
        return {"container_id": container.id, "port": host_port}

    def _build_image_sync(self, path: str, tag: str) -> None:
        """Build a Docker image from a local Dockerfile. Synchronous — call via run_in_executor."""
        print(f"[DockerService] Building image '{tag}' from {path} …")
        try:
            _image, build_logs = self.client.images.build(path=path, tag=tag, rm=True)
            for chunk in build_logs:
                if "stream" in chunk:
                    print(chunk["stream"], end="")
        except APIError as exc:
            raise ContainerStartError(f"Failed to build image from '{path}': {exc}")

    def _stop_container_sync(self, container_id: str) -> None:
        """Stop and remove a container by ID. Synchronous — call via run_in_executor."""
        try:
            print(f"[DockerService] Stopping container {container_id[:12]} …")
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
        network: Optional[str] = None,
        name: Optional[str] = None,
        volumes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Pull an image and run a container asynchronously."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self._run_container_sync, image_tag, internal_port, environment, cpu_limit, network, name, volumes),
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

    async def build_and_run_from_git(
        self,
        git_url: str,
        name: str,
        internal_port: int = 80,
        environment: Optional[Dict[str, str]] = None,
        cpu_limit: Optional[str] = None,
        network: Optional[str] = None,
        volumes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Clone a Git repository, build its Docker image, and run a container.

        Steps:
        1. Create a temporary directory.
        2. ``git clone <git_url>`` via asyncio subprocess (non-blocking).
        3. Build the Docker image (via run_in_executor so we don't block uvicorn).
        4. Run the container.
        5. Always clean up the temp directory in ``finally``.

        Args:
            git_url:       HTTPS/SSH URL of the repository to clone.
            name:          Container (and image tag) name.
            internal_port: Port exposed inside the container.
            environment:   Environment variables to inject.
            cpu_limit:     CPU limit string, e.g. ``"0.5"`` for half a core.
            network:       Docker network to attach the container to.

        Returns:
            Dict with ``container_id`` and ``port`` keys.

        Raises:
            GitCloneError:      If ``git clone`` exits with non-zero code.
            ContainerStartError: If the image build or container start fails.
        """
        self._assert_client()

        tmp_dir = tempfile.mkdtemp(prefix="envondemand_git_")
        image_tag = f"envondemand/{name.lower()}:latest"

        try:
            # --- Step 1: async git clone ---
            print(f"[DockerService] Cloning {git_url} into {tmp_dir} …")
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", git_url, tmp_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                raise GitCloneError(
                    f"git clone failed (exit {proc.returncode}) for '{git_url}': {err_msg}"
                )
            print(f"[DockerService] Clone successful.")

            # --- Step 2: build Docker image (blocking call → executor) ---
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                partial(self._build_image_sync, tmp_dir, image_tag),
            )

            # --- Step 3: run the container ---
            return await loop.run_in_executor(
                None,
                partial(
                    self._run_container_sync,
                    image_tag,
                    internal_port,
                    environment,
                    cpu_limit,
                    network,
                    name,
                    volumes,
                ),
            )

        finally:
            # Always clean up the cloned repo — even if an exception was raised.
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print(f"[DockerService] Cleaned up temp dir {tmp_dir}.")

    async def stop_container(self, container_id: str) -> None:
        """Asynchronously stop and remove a container."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(self._stop_container_sync, container_id))

    async def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        """Asynchronously retrieve the last N log lines of a container."""
        self._assert_client()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(self._get_container_logs_sync, container_id, tail)
        )
