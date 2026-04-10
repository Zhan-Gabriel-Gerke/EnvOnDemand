import httpx
import asyncio
import jwt
from datetime import datetime, timedelta, timezone

SECRET_KEY = "Fj5rt3de1x+C4LogO6XxC0nkcaulX8yoW8uL4ytIh20="
ALGORITHM = "HS256"

def create_token(user_id: str):
    expire = datetime.now(tz=timezone.utc) + timedelta(minutes=60)
    to_encode = {"sub": user_id, "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def test():
    token = create_token("1ceee8d6-c017-4ed2-94b5-906dd0e4b30d")
    async with httpx.AsyncClient() as c:
        headers = {"Authorization": f"Bearer {token}"}
        r = await c.get("http://localhost:8000/api/deployments", headers=headers)
        print(r.status_code)
        print(r.text)

if __name__ == "__main__":
    asyncio.run(test())
