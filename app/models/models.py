import enum
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy import (
    String,
    ForeignKey,
    func,
    Integer,
    Boolean,
    DateTime,
    Enum as SQLAlchemyEnum,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DeploymentStatus(str, enum.Enum):
    """Enum for the status of a deployment."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class AuditedEntityType(str, enum.Enum):
    """Enum for the types of entities that can be audited."""
    USER = "USER"
    PROJECT = "PROJECT"
    BLUEPRINT = "BLUEPRINT"
    DEPLOYMENT = "DEPLOYMENT"


class Role(Base):
    """
    Defines system access levels (admin, developer, viewer).
    Decoupled from User to support dynamic permission assignments without schema migrations.
    """
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    users: Mapped[List["User"]] = relationship("User", back_populates="role")


class User(Base):
    """Represents an application user identity."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), index=True)

    # Relationships
    role: Mapped["Role"] = relationship("Role", back_populates="users", lazy="selectin")
    quota: Mapped["UserQuota"] = relationship(
        "UserQuota", 
        back_populates="user", 
        uselist=False, 
        cascade="all, delete-orphan", 
        lazy="selectin"
    )
    projects: Mapped[List["Project"]] = relationship("Project", back_populates="owner", cascade="all, delete-orphan")
    audit_logs: Mapped[List["AuditLog"]] = relationship("AuditLog", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username='{self.username}')>"


class UserQuota(Base):
    """
    Tracks resource limits per user to prevent noisy-neighbor problems and resource exhaustion 
    on the underlying Docker daemon.
    """
    __tablename__ = "user_quotas"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    
    max_containers: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    active_containers: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    user: Mapped["User"] = relationship("User", back_populates="quota")


class Project(Base):
    """Represents a project, which is a collection of deployments."""
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_owner_project_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Relationships
    owner: Mapped["User"] = relationship("User", back_populates="projects", lazy="selectin")
    deployments: Mapped[List["Deployment"]] = relationship("Deployment", back_populates="project", cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, name='{self.name}')>"


class Blueprint(Base):
    """Represents a template for creating deployments. This should be treated as a read-only template after creation."""
    __tablename__ = "blueprints"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    image_tag: Mapped[str] = mapped_column(String(255))
    default_port: Mapped[int] = mapped_column(Integer)
    default_env_vars: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    cpu_limit: Mapped[Optional[str]] = mapped_column(String(50))

    # This relationship is for viewing which deployments were created from this blueprint,
    # but the deployment itself holds the canonical configuration.
    deployments: Mapped[List["Deployment"]] = relationship("Deployment", back_populates="blueprint")

    def __repr__(self) -> str:
        return f"<Blueprint(id={self.id}, name='{self.name}', image_tag='{self.image_tag}')>"


class Deployment(Base):
    """
    Represents a running or stopped container instance.
    It contains a snapshot of the configuration from its Blueprint at the time of creation.
    """
    __tablename__ = "deployments"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # --- Snapshot of Blueprint configuration ---
    image_tag: Mapped[str] = mapped_column(String(255))
    env_vars: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    cpu_limit: Mapped[Optional[str]] = mapped_column(String(50))
    # --- End of Snapshot ---

    status: Mapped[DeploymentStatus] = mapped_column(
        SQLAlchemyEnum(DeploymentStatus, name="deployment_status_enum"),
        default=DeploymentStatus.PENDING,
        server_default=DeploymentStatus.PENDING.value,
        index=True
    )
    container_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    internal_port: Mapped[int] = mapped_column(Integer)
    external_port: Mapped[Optional[int]] = mapped_column(Integer, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Foreign Keys
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # The blueprint_id is kept for reference, but if the blueprint is deleted, the deployment should remain.
    blueprint_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("blueprints.id", ondelete="SET NULL"), index=True)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="deployments")
    # This relationship is for reference. It can be nullable if the original blueprint is deleted.
    blueprint: Mapped[Optional["Blueprint"]] = relationship("Blueprint", back_populates="deployments")

    def __repr__(self) -> str:
        return f"<Deployment(id={self.id}, status='{self.status.value}', image_tag='{self.image_tag}')>"


class AuditLog(Base):
    """Represents a log entry for an action performed in the system."""
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[AuditedEntityType] = mapped_column(
        SQLAlchemyEnum(AuditedEntityType, name="audited_entity_type_enum"),
        index=True
    )
    entity_id: Mapped[str] = mapped_column(String(255))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action='{self.action}', entity_type='{self.entity_type.value}')>"
