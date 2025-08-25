import sqlite3
import json
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class OfflineManager:
    """
    Manages offline functionality with local SQLite caching
    Syncs with PostgreSQL when connection is available
    """

    def __init__(self, db_path: str = "offline_cache.db"):
        self.db_path = db_path
        self.init_offline_db()

    def init_offline_db(self):
        """Initialize SQLite database for offline caching"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Create tables for offline caching
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS offline_disposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    images_data TEXT NOT NULL,
                    synced INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cached_user_data (
                    user_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    last_updated TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cached_hotspots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    intensity REAL NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_updated TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cached_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    last_updated TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            conn.commit()
            conn.close()
            logger.info("Offline database initialized successfully")

        except Exception as e:
            logger.error(f"Error initializing offline database: {str(e)}")
            raise

    def cache_disposal_offline(self, user_id: str, latitude: float,
                               longitude: float,
                               images_data: List[str]) -> bool:
        """Cache disposal data for later sync when online"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                '''
                INSERT INTO offline_disposals (user_id, timestamp, latitude, longitude, images_data)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, datetime.utcnow().isoformat(), latitude, longitude,
                  json.dumps(images_data)))

            conn.commit()
            conn.close()
            logger.info(f"Cached disposal offline for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Error caching disposal offline: {str(e)}")
            return False

    def get_pending_disposals(self) -> List[Dict]:
        """Get all unsynced disposal records"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                SELECT id, user_id, timestamp, latitude, longitude, images_data
                FROM offline_disposals
                WHERE synced = 0
                ORDER BY timestamp ASC
            ''')

            disposals = []
            for row in cursor.fetchall():
                disposals.append({
                    'id': row[0],
                    'user_id': row[1],
                    'timestamp': row[2],
                    'latitude': row[3],
                    'longitude': row[4],
                    'images_data': json.loads(row[5])
                })

            conn.close()
            return disposals

        except Exception as e:
            logger.error(f"Error getting pending disposals: {str(e)}")
            return []

    def mark_disposal_synced(self, disposal_id: int) -> bool:
        """Mark a disposal as synced with server"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                '''
                UPDATE offline_disposals
                SET synced = 1
                WHERE id = ?
            ''', (disposal_id, ))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Error marking disposal as synced: {str(e)}")
            return False

    def cache_user_data(self, user_id: str, user_data: Dict) -> bool:
        """Cache user data for offline access"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                '''
                INSERT OR REPLACE INTO cached_user_data (user_id, data, last_updated)
                VALUES (?, ?, ?)
            ''', (user_id, json.dumps(user_data),
                  datetime.utcnow().isoformat()))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Error caching user data: {str(e)}")
            return False

    def get_cached_user_data(self, user_id: str) -> Optional[Dict]:
        """Get cached user data for offline mode"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                '''
                SELECT data FROM cached_user_data
                WHERE user_id = ?
            ''', (user_id, ))

            result = cursor.fetchone()
            conn.close()

            if result:
                return json.loads(result[0])
            return None

        except Exception as e:
            logger.error(f"Error getting cached user data: {str(e)}")
            return None

    def cache_hotspots(self, hotspots: List[Dict]) -> bool:
        """Cache hotspot data for offline map display"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Clear old hotspots
            cursor.execute('DELETE FROM cached_hotspots')

            # Insert new hotspots
            for hotspot in hotspots:
                cursor.execute(
                    '''
                    INSERT INTO cached_hotspots (latitude, longitude, intensity, expires_at)
                    VALUES (?, ?, ?, ?)
                ''', (hotspot['latitude'], hotspot['longitude'],
                      hotspot['intensity'],
                      hotspot.get('expires_at',
                                  datetime.utcnow().isoformat())))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Error caching hotspots: {str(e)}")
            return False

    def get_cached_hotspots(self) -> List[Dict]:
        """Get cached hotspots for offline map display"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                '''
                SELECT latitude, longitude, intensity, expires_at
                FROM cached_hotspots
                WHERE expires_at > ?
            ''', (datetime.utcnow().isoformat(), ))

            hotspots = []
            for row in cursor.fetchall():
                hotspots.append({
                    'latitude': row[0],
                    'longitude': row[1],
                    'intensity': row[2],
                    'expires_at': row[3]
                })

            conn.close()
            return hotspots

        except Exception as e:
            logger.error(f"Error getting cached hotspots: {str(e)}")
            return []

    def cache_challenges(self, challenges: List[Dict]) -> bool:
        """Cache challenge data for offline access"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            for challenge in challenges:
                cursor.execute(
                    '''
                    INSERT OR REPLACE INTO cached_challenges (challenge_id, data, last_updated)
                    VALUES (?, ?, ?)
                ''', (challenge['challenge_id'], json.dumps(challenge),
                      datetime.utcnow().isoformat()))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Error caching challenges: {str(e)}")
            return False

    def get_cached_challenges(self) -> List[Dict]:
        """Get cached challenges for offline mode"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('SELECT data FROM cached_challenges')

            challenges = []
            for row in cursor.fetchall():
                challenges.append(json.loads(row[0]))

            conn.close()
            return challenges

        except Exception as e:
            logger.error(f"Error getting cached challenges: {str(e)}")
            return []

    def is_online(self) -> bool:
        """Check if the device has internet connectivity"""
        try:
            import requests
            response = requests.get('http://www.google.com', timeout=5)
            return response.status_code == 200
        except:
            return False

    def sync_offline_data(self, app_context) -> Dict[str, int]:
        """Sync offline data with server when connection is available"""
        sync_stats = {'synced_disposals': 0, 'failed_disposals': 0}

        if not self.is_online():
            logger.info("Device is offline, skipping sync")
            return sync_stats

        try:
            with app_context:
                from models import db, User, Disposal, DailyDisposalLog
                from gemini_service import validate_disposal_with_ai_video

                pending_disposals = self.get_pending_disposals()

                for disposal in pending_disposals:
                    try:
                        # Process disposal through AI validation
                        # Note: Using first image as video data for compatibility
                        images_data = [
                            img.encode() for img in disposal['images_data']
                        ]
                        ai_result = validate_disposal_with_ai_video(
                            images_data[0] if images_data else b'')

                        if ai_result['success']:
                            # Save to main database
                            new_disposal = Disposal(
                                user_id=disposal['user_id'],
                                timestamp=datetime.fromisoformat(
                                    disposal['timestamp']),
                                latitude=disposal['latitude'],
                                longitude=disposal['longitude'],
                                waste_category=ai_result['waste_category'],
                                waste_sub_type=ai_result['waste_sub_type'],
                                points_awarded=10  # Standard points
                            )

                            db.session.add(new_disposal)

                            # Add to daily log for anti-cheat
                            daily_log = DailyDisposalLog(
                                user_id=disposal['user_id'],
                                date=datetime.fromisoformat(
                                    disposal['timestamp']).date(),
                                waste_sub_type=ai_result['waste_sub_type'])

                            db.session.add(daily_log)
                            db.session.commit()

                            # Mark as synced
                            self.mark_disposal_synced(disposal['id'])
                            sync_stats['synced_disposals'] += 1

                        else:
                            # Mark as synced but failed validation
                            self.mark_disposal_synced(disposal['id'])
                            sync_stats['failed_disposals'] += 1

                    except Exception as e:
                        logger.error(
                            f"Error syncing disposal {disposal['id']}: {str(e)}"
                        )
                        sync_stats['failed_disposals'] += 1
                        continue

                logger.info(
                    f"Sync completed: {sync_stats['synced_disposals']} synced, {sync_stats['failed_disposals']} failed"
                )

        except Exception as e:
            logger.error(f"Error during sync process: {str(e)}")

        return sync_stats

    def cleanup_old_data(self, days_old: int = 30) -> bool:
        """Clean up old cached data to save space"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cutoff_date = datetime.utcnow().isoformat()

            # Remove old synced disposals
            cursor.execute('''
                DELETE FROM offline_disposals
                WHERE synced = 1 AND created_at < datetime('now', '-{} days')
            '''.format(days_old))

            # Remove old cached data
            cursor.execute('''
                DELETE FROM cached_user_data
                WHERE last_updated < datetime('now', '-{} days')
            '''.format(days_old))

            cursor.execute('''
                DELETE FROM cached_hotspots
                WHERE last_updated < datetime('now', '-{} days')
            '''.format(days_old))

            cursor.execute('''
                DELETE FROM cached_challenges
                WHERE last_updated < datetime('now', '-{} days')
            '''.format(days_old))

            conn.commit()
            conn.close()
            logger.info(f"Cleaned up offline data older than {days_old} days")
            return True

        except Exception as e:
            logger.error(f"Error cleaning up old data: {str(e)}")
            return False
