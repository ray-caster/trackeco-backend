#!/usr/bin/env python3
"""
Migration script to initialize denormalized leaderboard data.
This script populates the leaderboard collection from existing user data.
"""

import logging
import sys
from google.cloud import firestore
from firebase_init import db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def migrate_leaderboard_data():
    """Migrate existing user data to denormalized leaderboard collection."""
    try:
        logging.info("Starting leaderboard data migration...")
        
        # Get all users
        users_ref = db.collection('users')
        users = list(users_ref.stream())
        
        logging.info(f"Found {len(users)} users to migrate")
        
        # Batch process users
        batch = db.batch()
        batch_size = 500
        processed = 0
        
        for i, user_doc in enumerate(users):
            user_data = user_doc.to_dict()
            leaderboard_ref = db.collection('leaderboard').document(user_doc.id)
            
            batch.set(leaderboard_ref, {
                'userId': user_doc.id,
                'totalPoints': user_data.get('totalPoints', 0),
                'displayName': user_data.get('displayName'),
                'avatarUrl': user_data.get('avatarUrl'),
                'migratedAt': firestore.SERVER_TIMESTAMP
            })
            
            # Commit batch every batch_size documents
            if (i + 1) % batch_size == 0:
                batch.commit()
                processed += batch_size
                logging.info(f"Processed {processed} users")
                batch = db.batch()
        
        # Commit final batch
        if len(users) % batch_size != 0:
            batch.commit()
            processed = len(users)
        
        logging.info(f"Successfully migrated {processed} users to leaderboard collection")
        
        # Update leaderboard stats
        from api.leaderboard_counters import calculate_and_update_leaderboard_stats
        stats = calculate_and_update_leaderboard_stats()
        logging.info(f"Updated leaderboard stats: {stats}")
        
        return True
        
    except Exception as e:
        logging.error(f"Migration failed: {e}", exc_info=True)
        return False

def verify_migration():
    """Verify that migration was successful."""
    try:
        users_count = len(list(db.collection('users').stream()))
        leaderboard_count = len(list(db.collection('leaderboard').stream()))
        
        logging.info(f"Verification: Users={users_count}, Leaderboard={leaderboard_count}")
        
        if users_count == leaderboard_count:
            logging.info("✓ Migration verification successful")
            return True
        else:
            logging.warning(f"⚠ Migration incomplete: {users_count} users vs {leaderboard_count} leaderboard entries")
            return False
            
    except Exception as e:
        logging.error(f"Verification failed: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    logging.info("Leaderboard Migration Tool")
    logging.info("=" * 50)
    
    success = migrate_leaderboard_data()
    if success:
        success = verify_migration()
    
    if success:
        logging.info("🎉 Migration completed successfully!")
        sys.exit(0)
    else:
        logging.error("❌ Migration failed")
        sys.exit(1)