# Chat SSE Connection Contract

This document describes the error codes and connection contract for the chat service's Server-Sent Events (SSE) streaming API.

## Error Codes

The chat service returns machine-readable error codes that frontends can use to handle specific conditions.

| Code | HTTP Status | Meaning | Client Action |
|------|-------------|---------|---------------|
| `auth_required` | 401 | No token provided | Prompt user to login |
| `token_expired` | 401 | JWT has expired | Refresh token, retry request |
| `token_invalid` | 401 | JWT is malformed or signature invalid | Re-authenticate |
| `auth_failed` | 401 | User not found or deactivated | Re-authenticate |
| `rate_limited` | 429 | Too many requests | Wait and retry with exponential backoff |
| `llm_unavailable` | 503 | LLM service is down | Show error, retry with backoff |
| `llm_timeout` | 504 | LLM response timed out | Show error, retry |
| `rag_error` | 503 | RAG/embedding service error | Show error, retry |
| `location_error` | 500 | Location resolution failed | Show error, fallback to no location |
| `session_error` | 500 | Session creation/lookup failed | Show error, retry |
| `server_error` | 500 | Internal server error | Show error, retry with backoff |
| `invalid_request` | 400 | Bad request format | Fix request and retry |

## Error Response Format

HTTP error responses include both a human-readable message and machine-readable error code:

```json
{
  "detail": {
    "message": "Token has expired",
    "error_code": "token_expired"
  }
}
```

## Streaming Error Chunks

During SSE streaming, errors are sent as StreamChunk objects:

```json
{
  "model": "A",
  "token": "",
  "done": true,
  "error": "LLM service timed out",
  "error_code": "llm_timeout"
}
```

## Recommended Client Flow

### 1. Before Opening SSE Connection

```javascript
// Check token expiry locally before connecting
const tokenExpiry = getTokenExpiry(accessToken);
const bufferMs = 30000; // 30 second buffer

if (Date.now() > tokenExpiry - bufferMs) {
  // Token will expire soon, refresh first
  accessToken = await refreshToken();
}

// Then connect
const eventSource = new EventSource('/api/v1/chat/stream', {
  headers: { 'Authorization': `Bearer ${accessToken}` }
});
```

### 2. Handling Connection Errors

```javascript
eventSource.onerror = async (event) => {
  eventSource.close();

  // Parse error response if available
  const error = parseErrorResponse(event);

  switch (error?.error_code) {
    case 'token_expired':
      // Refresh token and reconnect
      accessToken = await refreshToken();
      reconnect();
      break;

    case 'token_invalid':
    case 'auth_failed':
    case 'auth_required':
      // Need full re-authentication
      redirectToLogin();
      break;

    case 'rate_limited':
      // Wait and retry with exponential backoff
      await sleep(getBackoffDelay());
      reconnect();
      break;

    case 'llm_unavailable':
    case 'llm_timeout':
    case 'server_error':
      // Show error to user, offer retry
      showError('Service temporarily unavailable. Please try again.');
      break;

    default:
      showError('An unexpected error occurred.');
  }
};
```

### 3. Handling Stream Errors

```javascript
eventSource.addEventListener('message', (event) => {
  const chunk = JSON.parse(event.data);

  if (chunk.error_code) {
    // Handle in-stream error
    handleStreamError(chunk.error_code, chunk.error);
  } else if (chunk.token) {
    // Append token to response
    appendToken(chunk.token);
  }

  if (chunk.done) {
    eventSource.close();
  }
});
```

## Keepalive

### Ping Endpoint

The chat service provides a lightweight ping endpoint for keepalive checks:

```
GET /api/v1/chat/ping

Response:
{
  "status": "ok",
  "timestamp": "2024-01-15T10:30:00.000Z"
}
```

Use this for:
- Checking if the service is responsive before opening a connection
- Periodic keepalive checks when idle

### Health Endpoint

For detailed health information (database, LLM availability):

```
GET /api/v1/chat/health

Response:
{
  "status": "healthy",
  "service": "chat_service",
  "database": "connected",
  "llm": "connected",
  ...
}
```

## Connection Lifecycle

1. **Pre-flight**: Check token expiry, refresh if needed
2. **Connect**: Open SSE connection with Bearer token
3. **Stream**: Receive token chunks, handle errors inline
4. **Close**: Connection closes on `done: true` or error
5. **Retry**: On recoverable errors, implement exponential backoff

## Timeout Recommendations

| Operation | Recommended Timeout |
|-----------|-------------------|
| Token refresh | 5 seconds |
| SSE connection | 30 seconds |
| LLM response start | 30 seconds |
| Full LLM response | 120 seconds |
| Ping check | 5 seconds |

## Rate Limiting

Currently, rate limiting is not enforced at the chat service level. If `rate_limited` errors occur, they may come from upstream services.
