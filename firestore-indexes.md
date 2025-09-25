# Firestore Composite Indexes Required for TrackEco

This document lists all the composite indexes required for Firestore queries in the TrackEco backend.

## Required Indexes

### 1. Streak Reminder/Updater Queries
- **Collection**: `users`
- **Fields**: 
  - `currentStreak` (Ascending)
  - `lastStreakTimestamp` (Ascending)
- **Query**: 
  ```python
  users_ref.where('currentStreak', '>', 0).where('lastStreakTimestamp', '<', start_of_today_wib)
  ```
- **Files**: `tasks/streak_reminder.py`, `tasks/streak_updater.py`

### 2. Challenges Active Query
- **Collection**: `challenges`
- **Fields**:
  - `isActive` (Ascending)
  - `type` (Ascending) - for challenge generator
- **Query**:
  ```python
  db.collection('challenges').where('isActive', '==', True)
  db.collection('challenges').where('type', '==', challenge_type).where('isActive', '==', True)
  ```
- **Files**: `challenge_generator.py`, `tasks.py`, `api/gamification.py`, `init_challenges.py`

### 3. Username Check Query
- **Collection**: `users`
- **Fields**:
  - `username` (Ascending)
- **Query**:
  ```python
  users_ref.where('username', '==', req_data.username).limit(1)
  ```
- **Files**: `api/users.py`

### 4. Email Hashes Query
- **Collection**: `email_hashes`
- **Fields**:
  - `__name__` (Ascending) - for document ID in query
- **Query**:
  ```python
  db.collection('email_hashes').where(filter=firestore.FieldFilter.from_document_id("in", chunk))
  ```
- **Files**: `api/social.py`

### 5. Leaderboard Queries
- **Collection**: `users`
- **Fields**:
  - `totalPoints` (Descending)
  - `userId` (Ascending) - for cursor-based pagination
- **Queries**:
  ```python
  base_query.where("totalPoints", ">", cursor_points)
  base_query.where("totalPoints", "==", cursor_points).where("userId", "<=", last_doc_snapshot.id)
  ```
- **Files**: `api/gamification.py`

### 6. Uploads History Query
- **Collection**: `uploads`
- **Fields**:
  - `userId` (Ascending)
  - `timestamp` (Descending)
- **Query**:
  ```python
  db.collection('uploads').where("userId", "==", user_id).order_by("timestamp", direction=firestore.Query.DESCENDING)
  ```
- **Files**: `api/core.py`

## How to Create Indexes

1. Go to the [Firebase Console](https://console.firebase.google.com/)
2. Select your project
3. Go to Firestore Database > Indexes
4. Click "Add Index" and configure each index as listed above.

## Automatic Index Creation

Firestore may automatically create single-field indexes, but composite indexes must be created manually for queries involving multiple fields.

## Indexing Best Practices

- Create indexes for all queries to avoid performance issues.
- Monitor index usage and costs in the Firebase console.
- Consider the order of fields in composite indexes to match query patterns.