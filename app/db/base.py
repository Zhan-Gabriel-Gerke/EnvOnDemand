from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy declarative models.
    All ORM models will inherit from this class.
    """
    pass
