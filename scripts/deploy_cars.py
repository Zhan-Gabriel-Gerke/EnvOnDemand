
import httpx
import jwt
from datetime import datetime, timedelta, timezone
import os
import uuid

# Configuration (mirrored from app/core/config.py)
SECRET_KEY = "Fj5rt3de1x+C4LogO6XxC0nkcaulX8yoW8uL4ytIh20="
ALGORITHM = "HS256"
ADMIN_USER_ID = "86f1b9ce-7e91-4417-8587-e67d06367d43"
API_URL = "http://localhost:8000/api/deployments"

def create_token(user_id: str):
    expire = datetime.now(tz=timezone.utc) + timedelta(minutes=60)
    to_encode = {"sub": user_id, "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def deploy():
    token = create_token(ADMIN_USER_ID)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    # Generate unique suffix for this deployment
    suffix = uuid.uuid4().hex[:6]
    
    payload = {
        "network_name": f"cars-net-{suffix}",
        "containers": [
            {
                "name": f"db-{suffix}",
                "image": "postgres:15-alpine",
                "role": "database",
                "env_vars": {
                    "POSTGRES_USER": "cars_4utp_user",
                    "POSTGRES_PASSWORD": "bDRj9qgrgM6NoAwiucWOIUF8jfPv4DZd",
                    "POSTGRES_DB": "cars_4utp"
                },
                "ports": {5432: 5432}
            },
            {
                "name": f"app-{suffix}",
                "git_url": "https://github.com/Zhan-Gabriel-Gerke/Cars",
                "role": "frontend",
                "env_vars": {
                    "AUTH_SECRET": "af02826c1c6a6f34812673762b77689a8071c7d125ebd1378c3dee39073183cd",
                    "AUTH_URL": "http://localhost:${PORT}",
                    "DATABASE_URL": f"postgresql://cars_4utp_user:bDRj9qgrgM6NoAwiucWOIUF8jfPv4DZd@db-{suffix}:5432/cars_4utp",
                    "NODE_ENV": "production",
                    "PORT": "3000"
                },
                "ports": {3000: 3000}
            }
        ]
    }

    async with httpx.AsyncClient() as client:
        print(f"Sending deployment request to {API_URL}...")
        try:
            response = await client.post(API_URL, json=payload, headers=headers, timeout=30.0)
            if response.status_code == 202:
                print("Deployment accepted!")
                print(response.json())
            else:
                print(f"Error {response.status_code}: {response.text}")
        except Exception as e:
            print(f"Exception occurred: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(deploy())
