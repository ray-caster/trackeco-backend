import logging
from google.cloud import firestore
from .config import db

def update_user_points_counter(user_id: str, points_delta: int):
    """
    Atomically update a user's points counter and maintain leaderboard rankings.
    This should be called whenever a user's points change.
    """
    user_ref = db.collection('users').document(user_id)
    leaderboard_ref = db.collection('leaderboard').document(user_id)
    
    @firestore.transactional
    def update_in_transaction(transaction):
        # Get current user data
        user_doc = user_ref.get(transaction=transaction)
        if not user_doc.exists:
            logging.warning(f"User {user_id} not found during points update")
            return
        
        user_data = user_doc.to_dict()
        current_points = user_data.get('totalPoints', 0)
        new_points = current_points + points_delta
        
        # Update user document
        transaction.update(user_ref, {'totalPoints': new_points})
        
        # Update leaderboard entry
        transaction.set(leaderboard_ref, {
            'userId': user_id,
            'totalPoints': new_points,
            'displayName': user_data.get('displayName'),
            'avatarUrl': user_data.get('avatarUrl'),
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })
    
    try:
        update_in_transaction(db.transaction())
        logging.info(f"Successfully updated points for user {user_id}: +{points_delta}")
    except Exception as e:
        logging.error(f"Failed to update points for user {user_id}: {e}")

def initialize_leaderboard_entry(user_id: str, user_data: dict):
    """
    Initialize a leaderboard entry for a new user or when user data changes.
    """
    leaderboard_ref = db.collection('leaderboard').document(user_id)
    leaderboard_ref.set({
        'userId': user_id,
        'totalPoints': user_data.get('totalPoints', 0),
        'displayName': user_data.get('displayName'),
        'avatarUrl': user_data.get('avatarUrl'),
        'createdAt': firestore.SERVER_TIMESTAMP,
        'lastUpdated': firestore.SERVER_TIMESTAMP
    })

def get_leaderboard_stats():
    """
    Get pre-calculated leaderboard statistics.
    Returns total user count and other metrics.
    """
    stats_ref = db.collection('leaderboard_stats').document('global')
    stats_doc = stats_ref.get()
    
    if stats_doc.exists:
        return stats_doc.to_dict()
    
    # Fallback: calculate stats if not exists
    return calculate_and_update_leaderboard_stats()

def calculate_and_update_leaderboard_stats():
    """
    Calculate leaderboard statistics and update the stats document.
    This should be run periodically or when significant changes occur.
    """
    users_query = db.collection('users').stream()
    total_users = 0
    total_points = 0
    
    for user_doc in users_query:
        total_users += 1
        user_data = user_doc.to_dict()
        total_points += user_data.get('totalPoints', 0)
    
    stats_data = {
        'totalUsers': total_users,
        'totalPoints': total_points,
        'averagePoints': total_points / total_users if total_users > 0 else 0,
        'lastUpdated': firestore.SERVER_TIMESTAMP
    }
    
    db.collection('leaderboard_stats').document('global').set(stats_data)
    return stats_data

def batch_update_leaderboard_entries():
    """
    Batch update all leaderboard entries from user data.
    Useful for initial setup or data consistency checks.
    """
    batch = db.batch()
    users_query = db.collection('users').stream()
    
    for user_doc in users_query:
        user_data = user_doc.to_dict()
        leaderboard_ref = db.collection('leaderboard').document(user_doc.id)
        
        batch.set(leaderboard_ref, {
            'userId': user_doc.id,
            'totalPoints': user_data.get('totalPoints', 0),
            'displayName': user_data.get('displayName'),
            'avatarUrl': user_data.get('avatarUrl'),
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })
    
    try:
        batch.commit()
        logging.info("Batch leaderboard update completed successfully")
    except Exception as e:
        logging.error(f"Batch leaderboard update failed: {e}")