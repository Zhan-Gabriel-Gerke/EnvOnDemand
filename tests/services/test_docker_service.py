"""
tests/services/test_docker_service.py
Coverage tests for app.services.docker_service — all functions and branches.

Strategy
--------
- DockerService.__aenter__ calls docker.from_env() and client.ping() inside
  run_in_executor.  We mock both so no real daemon is needed.
- All _*_sync helpers are called directly on a pre-configured instance to
  avoid the async executor overhead while still hitting every branch.
- Public async methods delegate to the _sync helpers via run_in_executor;
  we verify they call through by mocking the _sync helper.
"""
import asyncio
import socket
import tempfile
import os
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call

from docker.errors import NotFound, APIError, ImageNotFound, BuildError

from app.services.docker_service import (
    DockerService,
    DockerServiceError,
    DockerImageError,
    ContainerStartError,
    GitCloneError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(client=None) -> DockerService:
    """Return a DockerService with a pre-set mock client (no context manager needed)."""
    svc = DockerService()
    svc.client = client or MagicMock()
    return svc


def _api_error(msg="api error") -> APIError:
    resp = MagicMock()
    resp.status_code = 500
    resp.reason = msg
    return APIError(msg, response=resp, explanation=msg)


# ---------------------------------------------------------------------------
# __init__ / context manager
# ---------------------------------------------------------------------------

def test_init_sets_client_to_none():
    svc = DockerService()
    assert svc.client is None


@pytest.mark.asyncio
async def test_aenter_success():
    with patch("app.services.docker_service.docker.from_env") as mock_env:
        mock_client = MagicMock()
        mock_env.return_value = mock_client
        async with DockerService() as svc:
            assert svc.client is mock_client


@pytest.mark.asyncio
async def test_aenter_raises_connection_error_on_failure():
    with patch("app.services.docker_service.docker.from_env", side_effect=Exception("daemon down")):
        with pytest.raises(ConnectionError):
            async with DockerService():
                pass


@pytest.mark.asyncio
async def test_aexit_closes_client():
    mock_client = MagicMock()
    with patch("app.services.docker_service.docker.from_env", return_value=mock_client):
        async with DockerService():
            pass
    mock_client.close.assert_called_once()


@pytest.mark.asyncio
async def test_aexit_with_no_client_does_not_raise():
    svc = DockerService()
    # client is None — __aexit__ should silently pass
    await svc.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_aexit_swallows_close_exception():
    mock_client = MagicMock()
    mock_client.close.side_effect = RuntimeError("close failed")
    with patch("app.services.docker_service.docker.from_env", return_value=mock_client):
        async with DockerService():
            pass  # __aexit__ must not propagate the error


# ---------------------------------------------------------------------------
# _assert_client
# ---------------------------------------------------------------------------

def test_assert_client_raises_when_none():
    svc = DockerService()
    with pytest.raises(RuntimeError):
        svc._assert_client()


def test_assert_client_passes_when_set():
    svc = _make_service()
    svc._assert_client()  # no exception


# ---------------------------------------------------------------------------
# _find_free_port_sync
# ---------------------------------------------------------------------------

def test_find_free_port_returns_int():
    svc = _make_service()
    port = svc._find_free_port_sync()
    assert isinstance(port, int)
    assert port > 0


# ---------------------------------------------------------------------------
# _run_container_sync — all branches
# ---------------------------------------------------------------------------

def _base_container_mock(network=None, host_port=32000, internal_ip="172.17.0.2"):
    """Return a mock container whose attrs mimic Docker Engine output."""
    container = MagicMock()
    container.id = "deadbeef" * 8
    net_key = network or "bridge"
    container.attrs = {
        "NetworkSettings": {
            "Ports": {"80/tcp": [{"HostPort": str(host_port)}]},
            "Networks": {
                net_key: {"IPAddress": internal_ip},
            },
        }
    }
    return container


def test_run_container_image_exists_locally():
    """Image found locally → no pull, container started."""
    svc = _make_service()
    container = _base_container_mock()
    svc.client.containers.run.return_value = container
    svc.client.networks.get.return_value = MagicMock()

    with patch.object(svc, "_find_free_port_sync", return_value=32000):
        result = svc._run_container_sync(
            image_tag="nginx:latest",
            internal_port=80,
            environment=None,
            cpu_limit=None,
        )

    assert result["container_id"] == container.id
    svc.client.images.get.assert_called_once_with("nginx:latest")
    svc.client.images.pull.assert_not_called()


def test_run_container_pulls_image_when_not_found():
    """Image not local → pulled."""
    svc = _make_service()
    svc.client.images.get.side_effect = NotFound("not found")
    container = _base_container_mock()
    svc.client.containers.run.return_value = container

    with patch.object(svc, "_find_free_port_sync", return_value=32000):
        result = svc._run_container_sync("nginx:latest", 80, None, None)

    svc.client.images.pull.assert_called_once_with("nginx:latest")
    assert result["port"] == 32000


def test_run_container_raises_image_not_found_on_pull_fail():
    svc = _make_service()
    svc.client.images.get.side_effect = NotFound("nf")
    svc.client.images.pull.side_effect = ImageNotFound("nf")

    with patch.object(svc, "_find_free_port_sync", return_value=32000):
        with pytest.raises(DockerImageError):
            svc._run_container_sync("bad:image", 80, None, None)


def test_run_container_raises_api_error_on_pull_fail():
    svc = _make_service()
    svc.client.images.get.side_effect = NotFound("nf")
    svc.client.images.pull.side_effect = _api_error()

    with patch.object(svc, "_find_free_port_sync", return_value=32000):
        with pytest.raises(DockerImageError):
            svc._run_container_sync("bad:image", 80, None, None)


def test_run_container_raises_docker_service_error_on_images_get_api_error():
    svc = _make_service()
    svc.client.images.get.side_effect = _api_error()

    with patch.object(svc, "_find_free_port_sync", return_value=32000):
        with pytest.raises(DockerServiceError):
            svc._run_container_sync("nginx:latest", 80, None, None)


def test_run_container_with_environment_vars():
    """${PORT} and ${HOST_PORT} are substituted in env values."""
    svc = _make_service()
    container = MagicMock()
    container.id = "deadbeef" * 8
    container.attrs = {
        "NetworkSettings": {
            "Ports": {"3000/tcp": [{"HostPort": "9000"}]},
            "Networks": {"bridge": {"IPAddress": "172.17.0.2"}},
        }
    }
    svc.client.containers.run.return_value = container

    with patch.object(svc, "_find_free_port_sync", return_value=9000):
        result = svc._run_container_sync(
            "img:tag", 3000,
            environment={"URL": "http://host:${PORT}", "EXT": "http://h:${HOST_PORT}", "NUM": 42},
            cpu_limit=None,
        )

    call_kwargs = svc.client.containers.run.call_args[1]
    env = call_kwargs["environment"]
    assert env["URL"] == "http://host:3000"
    assert env["EXT"] == "http://h:9000"
    assert env["NUM"] == 42  # non-str passthrough


def test_run_container_with_mem_limit_cpu_limit_volumes_name_network():
    """All optional kwargs are forwarded."""
    svc = _make_service()
    container = _base_container_mock(network="mynet", host_port=5000)
    svc.client.containers.run.return_value = container
    svc.client.networks.get.return_value = MagicMock()  # network exists

    with patch.object(svc, "_find_free_port_sync", return_value=5000):
        result = svc._run_container_sync(
            "img:tag", 80, None, "0.5", "256m", "mynet", "my-container",
            volumes={"/host/path": "/container/path"},
        )

    call_kwargs = svc.client.containers.run.call_args[1]
    assert call_kwargs["mem_limit"] == "256m"
    assert call_kwargs["network"] == "mynet"
    assert call_kwargs["name"] == "my-container"
    assert "nano_cpus" in call_kwargs
    assert "/host/path" in call_kwargs["volumes"]


def test_run_container_creates_network_when_not_found():
    """Network doesn't exist → created."""
    svc = _make_service()
    container = _base_container_mock(network="newnet")
    svc.client.containers.run.return_value = container
    svc.client.networks.get.side_effect = [NotFound("nf"), MagicMock()]  # first get→NotFound, second ok

    with patch.object(svc, "_find_free_port_sync", return_value=5000):
        svc._run_container_sync("img", 80, None, None, network="newnet")

    svc.client.networks.create.assert_called_once_with("newnet", driver="bridge")


def test_run_container_network_create_api_error_but_exists():
    """create() raises APIError but network exists on retry → continues."""
    svc = _make_service()
    container = _base_container_mock(network="racenet")
    svc.client.containers.run.return_value = container
    svc.client.networks.get.side_effect = [NotFound("nf"), MagicMock()]
    svc.client.networks.create.side_effect = _api_error("already exists")

    with patch.object(svc, "_find_free_port_sync", return_value=5000):
        svc._run_container_sync("img", 80, None, None, network="racenet")


def test_run_container_network_create_api_error_still_not_found():
    """create() raises APIError and network still doesn't exist → DockerServiceError."""
    svc = _make_service()
    svc.client.networks.get.side_effect = NotFound("nf")
    svc.client.networks.create.side_effect = _api_error("hard fail")

    with patch.object(svc, "_find_free_port_sync", return_value=5000):
        with pytest.raises(DockerServiceError):
            svc._run_container_sync("img", 80, None, None, network="badnet")


def test_run_container_start_api_error():
    """containers.run raises APIError → ContainerStartError."""
    svc = _make_service()
    svc.client.containers.run.side_effect = _api_error()

    with patch.object(svc, "_find_free_port_sync", return_value=5000):
        with pytest.raises(ContainerStartError):
            svc._run_container_sync("img", 80, None, None)


def test_run_container_no_port_binding_raises():
    """Container started but no host port bound → ContainerStartError."""
    svc = _make_service()
    container = MagicMock()
    container.id = "abc" * 12
    container.attrs = {"NetworkSettings": {"Ports": {}, "Networks": {"bridge": {"IPAddress": "1.2.3.4"}}}}
    svc.client.containers.run.return_value = container

    with patch.object(svc, "_find_free_port_sync", return_value=5000):
        with pytest.raises(ContainerStartError):
            svc._run_container_sync("img", 80, None, None)


def test_run_container_network_ip_fallback_to_first():
    """When requested network key is absent, takes IP from first network."""
    svc = _make_service()
    container = MagicMock()
    container.id = "x" * 32
    container.attrs = {
        "NetworkSettings": {
            "Ports": {"80/tcp": [{"HostPort": "9999"}]},
            "Networks": {"bridge": {"IPAddress": "10.0.0.1"}},
        }
    }
    svc.client.containers.run.return_value = container
    svc.client.networks.get.return_value = MagicMock()

    with patch.object(svc, "_find_free_port_sync", return_value=9999):
        result = svc._run_container_sync("img", 80, None, None, network="mynet")

    assert result["ip"] == "10.0.0.1"


def test_run_container_no_internal_port_skips_port_discovery():
    """internal_port=0 → no port mapping, no host_port discovery."""
    svc = _make_service()
    container = MagicMock()
    container.id = "y" * 32
    container.attrs = {
        "NetworkSettings": {
            "Ports": {"0/tcp": None},
            "Networks": {"bridge": {"IPAddress": "10.0.0.2"}},
        }
    }
    svc.client.containers.run.return_value = container

    # internal_port=0 → falsy → host_port=None, port_mapping={}
    # bindings will be None → ContainerStartError expected
    with pytest.raises(ContainerStartError):
        svc._run_container_sync("img", 0, None, None)


# ---------------------------------------------------------------------------
# _build_image_sync — all branches
# ---------------------------------------------------------------------------

def test_build_image_sync_success():
    svc = _make_service()
    fake_logs = [{"stream": "Step 1/2\n"}, {"stream": "Step 2/2\n"}, {"other": "ignored"}]
    svc.client.images.build.return_value = (MagicMock(), iter(fake_logs))

    result = svc._build_image_sync("/tmp/repo", "myapp:latest")
    assert "Step 1/2" in result


def test_build_image_sync_build_error():
    svc = _make_service()
    err = BuildError("build failed", [{"stream": "ERROR LINE\n"}, {"other": True}])
    svc.client.images.build.side_effect = err

    with pytest.raises(ContainerStartError):
        svc._build_image_sync("/tmp/repo", "tag")


def test_build_image_sync_generic_exception():
    svc = _make_service()
    svc.client.images.build.side_effect = RuntimeError("unexpected")

    with pytest.raises(ContainerStartError):
        svc._build_image_sync("/tmp/repo", "tag")


# ---------------------------------------------------------------------------
# _stop_container_sync — all branches
# ---------------------------------------------------------------------------

def test_stop_container_sync_success():
    svc = _make_service()
    container = MagicMock()
    svc.client.containers.get.return_value = container
    svc._stop_container_sync("abc123" * 3)
    container.stop.assert_called_once_with(timeout=10)


def test_stop_container_sync_not_found():
    svc = _make_service()
    svc.client.containers.get.side_effect = NotFound("nf")
    svc._stop_container_sync("abc123" * 3)  # must not raise


def test_stop_container_sync_api_error():
    svc = _make_service()
    svc.client.containers.get.side_effect = _api_error()
    with pytest.raises(DockerServiceError):
        svc._stop_container_sync("abc123" * 3)


# ---------------------------------------------------------------------------
# _start_container_sync — all branches
# ---------------------------------------------------------------------------

def test_start_container_sync_success():
    svc = _make_service()
    container = MagicMock()
    svc.client.containers.get.return_value = container
    svc._start_container_sync("abc123" * 3)
    container.start.assert_called_once()


def test_start_container_sync_not_found():
    svc = _make_service()
    svc.client.containers.get.side_effect = NotFound("nf")
    with pytest.raises(DockerServiceError):
        svc._start_container_sync("abc123" * 3)


def test_start_container_sync_api_error():
    svc = _make_service()
    svc.client.containers.get.side_effect = _api_error()
    with pytest.raises(DockerServiceError):
        svc._start_container_sync("abc123" * 3)


# ---------------------------------------------------------------------------
# _remove_container_sync — all branches
# ---------------------------------------------------------------------------

def test_remove_container_sync_success():
    svc = _make_service()
    container = MagicMock()
    svc.client.containers.get.return_value = container
    svc._remove_container_sync("abc123" * 3)
    container.remove.assert_called_once_with(force=True)


def test_remove_container_sync_not_found():
    svc = _make_service()
    svc.client.containers.get.side_effect = NotFound("nf")
    svc._remove_container_sync("abc123" * 3)  # must not raise


def test_remove_container_sync_api_error():
    svc = _make_service()
    svc.client.containers.get.side_effect = _api_error()
    with pytest.raises(DockerServiceError):
        svc._remove_container_sync("abc123" * 3)


# ---------------------------------------------------------------------------
# _get_container_logs_sync — all branches
# ---------------------------------------------------------------------------

def test_get_container_logs_sync_success():
    svc = _make_service()
    container = MagicMock()
    container.logs.return_value = b"log line\n"
    svc.client.containers.get.return_value = container
    result = svc._get_container_logs_sync("abc123" * 3, tail=50)
    assert result == "log line\n"


def test_get_container_logs_sync_not_found():
    svc = _make_service()
    svc.client.containers.get.side_effect = NotFound("nf")
    with pytest.raises(DockerServiceError):
        svc._get_container_logs_sync("abc123" * 3)


def test_get_container_logs_sync_api_error():
    svc = _make_service()
    svc.client.containers.get.side_effect = _api_error()
    with pytest.raises(DockerServiceError):
        svc._get_container_logs_sync("abc123" * 3)


# ---------------------------------------------------------------------------
# _create_volume_sync
# ---------------------------------------------------------------------------

def test_create_volume_sync_success():
    svc = _make_service()
    svc._create_volume_sync("myvol")
    svc.client.volumes.create.assert_called_once_with(name="myvol")


def test_create_volume_sync_api_error():
    svc = _make_service()
    svc.client.volumes.create.side_effect = _api_error()
    with pytest.raises(DockerServiceError):
        svc._create_volume_sync("myvol")


# ---------------------------------------------------------------------------
# _remove_volume_sync — all branches
# ---------------------------------------------------------------------------

def test_remove_volume_sync_success():
    svc = _make_service()
    volume = MagicMock()
    svc.client.volumes.get.return_value = volume
    svc._remove_volume_sync("myvol")
    volume.remove.assert_called_once_with(force=True)


def test_remove_volume_sync_not_found():
    svc = _make_service()
    svc.client.volumes.get.side_effect = NotFound("nf")
    svc._remove_volume_sync("myvol")  # must not raise (pass)


def test_remove_volume_sync_api_error_in_use():
    svc = _make_service()
    svc.client.volumes.get.return_value = MagicMock()
    svc.client.volumes.get.return_value.remove.side_effect = _api_error("volume is in use")
    with pytest.raises(DockerServiceError, match="in use"):
        svc._remove_volume_sync("myvol")


def test_remove_volume_sync_api_error_other():
    svc = _make_service()
    svc.client.volumes.get.return_value = MagicMock()
    svc.client.volumes.get.return_value.remove.side_effect = _api_error("other failure")
    with pytest.raises(DockerServiceError):
        svc._remove_volume_sync("myvol")


# ---------------------------------------------------------------------------
# _remove_network_sync — all branches
# ---------------------------------------------------------------------------

def test_remove_network_sync_success():
    svc = _make_service()
    network = MagicMock()
    svc.client.networks.get.return_value = network
    svc._remove_network_sync("mynet")
    network.remove.assert_called_once()


def test_remove_network_sync_not_found():
    svc = _make_service()
    svc.client.networks.get.side_effect = NotFound("nf")
    svc._remove_network_sync("mynet")  # must not raise


def test_remove_network_sync_api_error_active_endpoints():
    svc = _make_service()
    network = MagicMock()
    network.remove.side_effect = _api_error("has active endpoints")
    svc.client.networks.get.return_value = network
    with pytest.raises(DockerServiceError, match="active endpoints"):
        svc._remove_network_sync("mynet")


def test_remove_network_sync_api_error_other():
    svc = _make_service()
    network = MagicMock()
    network.remove.side_effect = _api_error("other reason")
    svc.client.networks.get.return_value = network
    with pytest.raises(DockerServiceError):
        svc._remove_network_sync("mynet")


# ---------------------------------------------------------------------------
# cleanup_repo
# ---------------------------------------------------------------------------

def test_cleanup_repo():
    tmp = tempfile.mkdtemp()
    svc = _make_service()
    svc.cleanup_repo(tmp)
    assert not os.path.exists(tmp)


# ---------------------------------------------------------------------------
# Public async methods — delegate to sync helpers via run_in_executor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_run_container_delegates():
    svc = _make_service()
    expected = {"container_id": "abc", "port": 80, "ip": "1.2.3.4"}
    with patch.object(svc, "_run_container_sync", return_value=expected):
        result = await svc.run_container("img", 80)
    assert result == expected


@pytest.mark.asyncio
async def test_async_stop_container_delegates():
    svc = _make_service()
    with patch.object(svc, "_stop_container_sync") as mock_stop:
        await svc.stop_container("cid123")
    mock_stop.assert_called_once_with("cid123")


@pytest.mark.asyncio
async def test_async_start_container_delegates():
    svc = _make_service()
    with patch.object(svc, "_start_container_sync") as mock_start:
        await svc.start_container("cid123")
    mock_start.assert_called_once_with("cid123")


@pytest.mark.asyncio
async def test_async_remove_container_delegates():
    svc = _make_service()
    with patch.object(svc, "_remove_container_sync") as mock_rm:
        await svc.remove_container("cid123")
    mock_rm.assert_called_once_with("cid123")


@pytest.mark.asyncio
async def test_async_get_container_logs_delegates():
    svc = _make_service()
    with patch.object(svc, "_get_container_logs_sync", return_value="logs") as mock_logs:
        result = await svc.get_container_logs("cid123", tail=50)
    assert result == "logs"
    mock_logs.assert_called_once_with("cid123", 50)


@pytest.mark.asyncio
async def test_async_create_volume_delegates():
    svc = _make_service()
    with patch.object(svc, "_create_volume_sync") as mock_cv:
        await svc.create_volume("vol1")
    mock_cv.assert_called_once_with("vol1")


@pytest.mark.asyncio
async def test_async_remove_volume_delegates():
    svc = _make_service()
    with patch.object(svc, "_remove_volume_sync") as mock_rv:
        await svc.remove_volume("vol1")
    mock_rv.assert_called_once_with("vol1")


@pytest.mark.asyncio
async def test_async_remove_network_delegates():
    svc = _make_service()
    with patch.object(svc, "_remove_network_sync") as mock_rn:
        await svc.remove_network("net1")
    mock_rn.assert_called_once_with("net1")


@pytest.mark.asyncio
async def test_async_build_image_delegates():
    svc = _make_service()
    with patch.object(svc, "_build_image_sync", return_value="build logs") as mock_bi:
        result = await svc.build_image("/tmp/dir", "tag:latest")
    assert result == "build logs"
    mock_bi.assert_called_once_with("/tmp/dir", "tag:latest")


# ---------------------------------------------------------------------------
# clone_repo — success and failure branches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clone_repo_success():
    svc = _make_service()
    proc_mock = AsyncMock()
    proc_mock.returncode = 0
    proc_mock.communicate = AsyncMock(return_value=(b"", b""))

    with patch("app.services.docker_service.asyncio.create_subprocess_exec", return_value=proc_mock):
        tmp_dir = await svc.clone_repo("https://github.com/example/repo")

    assert os.path.exists(tmp_dir)
    import shutil; shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_clone_repo_failure_raises_git_clone_error():
    svc = _make_service()
    proc_mock = AsyncMock()
    proc_mock.returncode = 128
    proc_mock.communicate = AsyncMock(return_value=(b"", b"fatal: repo not found"))

    with patch("app.services.docker_service.asyncio.create_subprocess_exec", return_value=proc_mock):
        with pytest.raises(GitCloneError):
            await svc.clone_repo("https://github.com/bad/repo")
