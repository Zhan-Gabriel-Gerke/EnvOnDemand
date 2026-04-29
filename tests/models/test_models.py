"""
tests/models/test_models.py
Tests for __repr__ methods of all ORM models (app/models/models.py).
"""
import uuid
import pytest

from app.models.models import (
    User, Project, Blueprint, Deployment, DeploymentContainer,
    AuditLog, Volume, DeploymentStatus, ContainerStatus, AuditedEntityType,
)


def test_user_repr():
    u = User()
    u.id = uuid.uuid4()
    u.username = "alice"
    assert "alice" in repr(u)


def test_project_repr():
    p = Project()
    p.id = uuid.uuid4()
    p.name = "myproject"
    assert "myproject" in repr(p)


def test_blueprint_repr():
    b = Blueprint()
    b.id = uuid.uuid4()
    b.name = "bp"
    b.image_tag = "img:latest"
    assert "bp" in repr(b)


def test_deployment_repr():
    d = Deployment()
    d.id = uuid.uuid4()
    d.network_name = "net1"
    d.status = DeploymentStatus.RUNNING
    assert "net1" in repr(d)


def test_deployment_container_repr():
    c = DeploymentContainer()
    c.name = "web"
    c.role = "app"
    c.status = ContainerStatus.RUNNING
    c.lifecycle_phase = "STARTING"
    assert "web" in repr(c)


def test_audit_log_repr():
    a = AuditLog()
    a.id = uuid.uuid4()
    a.action = "CREATE"
    a.entity_type = AuditedEntityType.DEPLOYMENT
    assert "CREATE" in repr(a)


def test_volume_repr():
    v = Volume()
    v.id = uuid.uuid4()
    v.name = "myvol"
    assert "myvol" in repr(v)
