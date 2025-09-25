# TrackEco API Pagination Documentation

## Overview

This document describes the comprehensive pagination implementation for all list endpoints in the TrackEco backend. The system uses cursor-based pagination with Redis for efficient state management, providing better performance than traditional offset-based pagination.

## Pagination Parameters

All paginated endpoints support the following query parameters:

| Parameter | Type | Description | Default | Max |
|-----------|------|-------------|---------|-----|
| `limit` | integer | Number of items to return per page | 20 | 100 |
| `cursor` | string | Opaque cursor for pagination (base64 encoded) | null | - |

## Response Format

All paginated endpoints return responses in the following standardized format:

```json
{
  "data": [...],
  "pagination": {
    "has_more": true,
    "next_cursor": "base64_encoded_cursor_string",
    "total_count": 150
  }
}
```

## Endpoints with Pagination Support

### 1. `/challenges` - Get Active Challenges

**Endpoint**: `GET /api/challenges`

**Description**: Returns paginated list of active challenges

**Parameters**:
- `limit` (optional): Number of challenges to return (default: 20, max: 100)
- `cursor` (optional): Pagination cursor

**Example Request**:
```bash
GET /api/challenges?limit=10&cursor=eyJuZXh0X2luZGV4IjoxMCwidG90YWxfaXRlbXMiOjUwfQ==
```

**Example Response**:
```json
{
  "data": [
    {
      "challengeId": "challenge-1",
      "description": "Reduce plastic usage",
      "points": 50,
      "expiresAt": "2025-12-31T23:59:59Z"
    }
  ],
  "pagination": {
    "has_more": true,
    "next_cursor": "eyJuZXh0X2luZGV4IjoxMCwidG90YWxfaXRlbXMiOjUwfQ==",
    "total_count": 50
  }
}
```

### 2. `/friends` - Get Friend Data

**Endpoint**: `GET /api/friends`

**Description**: Returns paginated friend data including friends list, sent requests, and received requests

**Parameters** (each section can be paginated independently):
- `friends_limit` (optional): Number of friends to return
- `friends_cursor` (optional): Friends pagination cursor
- `sent_limit` (optional): Number of sent requests to return
- `sent_cursor` (optional): Sent requests pagination cursor
- `received_limit` (optional): Number of received requests to return
- `received_cursor` (optional): Received requests pagination cursor

**Example Request**:
```bash
GET /api/friends?friends_limit=15&sent_limit=5&received_limit=10
```

**Example Response**:
```json
{
  "friends": {
    "data": [...],
    "pagination": {
      "has_more": true,
      "next_cursor": "friends_cursor_string",
      "total_count": 45
    }
  },
  "sentRequests": {
    "data": [...],
    "pagination": {
      "has_more": false,
      "next_cursor": null,
      "total_count": 8
    }
  },
  "receivedRequests": {
    "data": [...],
    "pagination": {
      "has_more": true,
      "next_cursor": "received_cursor_string",
      "total_count": 25
    }
  }
}
```

### 3. `/history` - Get User History

**Endpoint**: `GET /api/history`

**Description**: Returns paginated user upload history

**Parameters**:
- `limit` (optional): Number of history items to return (default: 20, max: 100)
- `cursor` (optional): Pagination cursor

**Example Request**:
```bash
GET /api/history?limit=25&cursor=eyJuZXh0X2luZGV4IjoyNSwidG90YWxfaXRlbXMiOjEyMH0=
```

**Example Response**:
```json
{
  "data": [
    {
      "uploadId": "upload-123",
      "status": "COMPLETED",
      "timestamp": "2025-09-25T10:30:00Z",
      "pointsEarned": 75
    }
  ],
  "pagination": {
    "has_more": true,
    "next_cursor": "eyJuZXh0X2luZGV4IjoyNSwidG90YWxfaXRlbXMiOjEyMH0=",
    "total_count": 120
  }
}
```

## Backward Compatibility

All endpoints maintain backward compatibility:

1. **No parameters**: Returns first page with default limit
2. **Invalid parameters**: Returns appropriate error responses
3. **Expired cursors**: Returns first page with appropriate error handling

## Error Handling

### Invalid Parameters
```json
{
  "error": "Invalid request",
  "details": "Limit must be a positive integer"
}
```

### Invalid Cursor
```json
{
  "error": "Invalid request",
  "details": "Invalid cursor format"
}
```

### Expired Cursor
```json
{
  "error": "Pagination state expired",
  "details": "Please make a new request without cursor"
}
```

## Implementation Details

### Redis Integration
- Pagination state stored in Redis with 5-minute TTL
- Cursors are base64-encoded JSON objects containing pagination metadata
- Automatic cleanup of expired pagination states

### Performance Considerations
- Cursor-based pagination avoids performance issues with large offsets
- Redis caching reduces database load for repeated pagination requests
- Default limits prevent excessive data transfer

## Client Implementation Guidelines

1. **First Request**: Omit cursor parameter to get first page
2. **Subsequent Requests**: Use `next_cursor` from previous response
3. **Error Handling**: Handle expired cursors by restarting from first page
4. **Rate Limiting**: Respect server limits and implement appropriate retry logic

## Example Client Code

```javascript
// JavaScript example for paginating through challenges
async function fetchAllChallenges() {
  let cursor = null;
  let allChallenges = [];
  let hasMore = true;
  
  while (hasMore) {
    const params = new URLSearchParams();
    if (cursor) params.append('cursor', cursor);
    params.append('limit', '20');
    
    const response = await fetch(`/api/challenges?${params}`);
    const data = await response.json();
    
    allChallenges = allChallenges.concat(data.data);
    hasMore = data.pagination.has_more;
    cursor = data.pagination.next_cursor;
  }
  
  return allChallenges;
}
```

## Testing

Test pagination with various scenarios:
- Empty results
- Single page results
- Multiple page results
- Invalid parameters
- Expired cursors
- Edge cases (limits, boundaries)

## Changelog

### v1.0.0 (2025-09-25)
- Initial pagination implementation
- Cursor-based pagination with Redis
- Support for `/challenges`, `/friends`, and `/history` endpoints
- Backward compatibility maintained
- Comprehensive error handling