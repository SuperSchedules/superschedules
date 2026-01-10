# Embedding Service Deployment (IAC Prompt)

This document provides instructions for deploying the embedding microservice in the superschedules_IAC repository.

## Overview

The embedding service is a lightweight FastAPI app that provides sentence embeddings via HTTP. It runs as a single-worker service to share one model instance (~500MB) across all Django and FastAPI chat workers.

## Service Details

- **Port**: 8003
- **Health endpoint**: `GET /health`
- **Main endpoint**: `POST /embed` (accepts `{"texts": ["text1", "text2"]}`)
- **Model**: `all-MiniLM-L6-v2` (384-dimensional embeddings)
- **Workers**: Must be 1 (to share model instance)
- **Memory**: ~600MB (500MB model + overhead)
- **Startup time**: ~10-15 seconds (model loading + warmup)

## IAC Implementation Prompt

Copy this prompt to the superschedules_IAC repository to implement the deployment:

---

### Task: Add Embedding Service to ECS Deployment

**Context**: The backend now includes an embedding microservice that provides sentence embeddings via HTTP. This service must run as a separate ECS task/service alongside the existing Django and chat services.

**Architecture**:
```
┌──────────────┐     ┌──────────────┐
│ Django API   │     │ Chat Service │
│ (N workers)  │     │ (N workers)  │
└──────┬───────┘     └──────┬───────┘
       │                    │
       └────────┬───────────┘
                ▼
       ┌──────────────────┐
       │ Embedding Service │  ← Single instance, internal only
       │ (1 worker)        │
       └──────────────────┘
```

**Requirements**:

1. **New ECS Task Definition** (`embedding_service`):
   - Image: Same as Django image (superschedules)
   - Command: `["python", "-m", "uvicorn", "embedding_service.app:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]`
   - Port: 8003
   - Memory: 1024 MB (minimum)
   - CPU: 512 (0.5 vCPU)
   - Health check: `GET /health` with 200 response

2. **ECS Service** (`embedding-service`):
   - Desired count: 1 (IMPORTANT: only 1 task, single worker)
   - No auto-scaling (model should stay warm)
   - Internal-only (no public ALB target group)
   - Service discovery via AWS Cloud Map or internal ALB

3. **Service Discovery**:
   - Register with Cloud Map namespace as `embedding.superschedules.local`
   - Or use internal ALB target group on port 8003

4. **Environment Variables** (for Django/Chat services):
   - Add `EMBEDDING_SERVICE_URL=http://embedding.superschedules.local:8003`
   - This tells the chat service to use the embedding microservice

5. **Security Group**:
   - Allow inbound 8003 from Django/Chat security groups
   - No public internet access needed

6. **Logging**:
   - CloudWatch log group: `/ecs/embedding-service`

**Files to modify**:
- `terraform/prod/ecs.tf` - Add task definition and service
- `terraform/prod/templates/user_data.sh.tftpl` - Add EMBEDDING_SERVICE_URL env var
- `terraform/prod/alb.tf` - Add internal target group (if using internal ALB)
- `terraform/prod/service_discovery.tf` - Add Cloud Map service (if using service discovery)

**Dockerfile** (no changes needed - same image):
The embedding service is part of the same codebase and image. Just use a different command.

**Testing**:
```bash
# Health check
curl http://embedding-service:8003/health

# Test embedding
curl -X POST http://embedding-service:8003/embed \
  -H "Content-Type: application/json" \
  -d '{"texts": ["hello world"]}'
```

**Rollout strategy**:
1. Deploy embedding service first
2. Wait for health check to pass
3. Update Django/Chat services with EMBEDDING_SERVICE_URL
4. Rolling deployment of Django/Chat services

---

## Docker Compose (for local testing)

Add to `docker-compose.yml`:

```yaml
  embedding:
    build: .
    command: python -m uvicorn embedding_service.app:app --host 0.0.0.0 --port 8003 --workers 1
    ports:
      - "8003:8003"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8003/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  chat:
    # ... existing config ...
    environment:
      - EMBEDDING_SERVICE_URL=http://embedding:8003
    depends_on:
      embedding:
        condition: service_healthy

  django:
    # ... existing config ...
    environment:
      - EMBEDDING_SERVICE_URL=http://embedding:8003
    depends_on:
      embedding:
        condition: service_healthy
```

## Fallback Behavior

If `EMBEDDING_SERVICE_URL` is not set or the service is unavailable, the embedding client will fall back to loading the model locally. This ensures the application still works during deployment or if the embedding service has issues, but will use more memory per worker.
