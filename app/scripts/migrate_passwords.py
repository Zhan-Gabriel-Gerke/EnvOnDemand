import asyncio
import sys
import os

# Add the project directory to the sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy.future import select
from app.db.session import AsyncSessionLocal
from app.models.models import User
from app.core.security import get_password_hash

async def migrate_passwords():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        
        migrated_count = 0
        for user in users:
            # Simple check: bcrypt hashes start with $2b$ or $2a$ and are 60 chars long
            if not user.hashed_password.startswith("$2"):
                print(f"Migrating password for user: {user.username}")
                user.hashed_password = get_password_hash(user.hashed_password)
                migrated_count += 1
                
        if migrated_count > 0:
            await session.commit()
            print(f"Successfully migrated {migrated_count} user passwords to bcrypt.")
        else:
            print("No plaintext passwords found to migrate.")

if __name__ == "__main__":
    asyncio.run(migrate_passwords())
