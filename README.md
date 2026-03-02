# EnvOnDemand Setup & Run Guide

## Prerequisites
- **Python 3.10+** (Recommend 3.12)
- **Docker Desktop** (must be installed and running)
- **PostgreSQL Client** (optional but recommended)

## 1. Environment Setup

### Create Virtual Environment
```bash
python -m venv .venv
# Activate:
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
```

### Install Dependencies
```bash
pip install -r requirements.txt
```

### Configure Environment Variables
Copy `.env.example` to `.env` and adjust if needed:
```bash
cp .env.example .env
```
Ensure `DATABASE_URL` matches your local setup or Docker Compose.
**Crucial:** Ensure `DOCKER_HOST` is set correctly if running on Windows (e.g., `npipe:////./pipe/docker_engine`).

## 2. Start Infrastructure (Database)

Run PostgreSQL via Docker Compose:
```bash
docker-compose up -d
```
This starts a Postgres container on port 5432.

## 3. Database Migrations

Apply Alembic migrations to create the schema:
```bash
alembic upgrade head
```

## 4. Run the API Server

Start the FastAPI server using Uvicorn:
```bash
uvicorn app.main:app --reload
```
- API Docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Root: [http://localhost:8000/](http://localhost:8000/)

## 5. Verify Deployment

To test a deployment:
1. Ensure Docker Desktop is running.
2. Send a `POST /deployments` request (via Swagger UI).
3. The system will pull the image and start a container in the background.

## Troubleshooting

- **`ConnectionError`**: Ensure Docker Desktop is running.
- **Database Error**: Ensure `docker-compose up` is running and `.env` credentials match.
