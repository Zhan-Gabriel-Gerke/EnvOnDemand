import asyncio
from app.db.base import Base
from app.db.session import engine
from app.models.models import Volume  # Ensure all models are loaded

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

if __name__ == "__main__":
    asyncio.run(init_db())
