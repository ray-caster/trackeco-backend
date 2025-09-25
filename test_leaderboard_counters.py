#!/usr/bin/env python3
"""
Test script for the denormalized leaderboard counter system.
This script demonstrates the functionality and cost savings.
"""

import logging
from google.cloud import firestore
from firebase_init import db
from api.leaderboard_counters import update_user_points_counter, get_leaderboard_stats, batch_update_leaderboard_entries

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_points_update():
    """Test updating points and verifying denormalized counter."""
    logging.info("Testing points update mechanism...")
    
    # Simulate a user ID (in real usage, this would be an actual user ID)
    test_user_id = "test_user_123"
    initial_points = 100
    points_delta = 50
    
    # Create a test user document if it doesn't exist
    user_ref = db.collection('users').document(test_user_id)
    if not user_ref.get().exists:
        user_ref.set({
            'userId': test_user_id,
            'displayName': 'Test User',
            'totalPoints': initial_points,
            'avatarUrl': None
        })
        logging.info(f"Created test user {test_user_id} with {initial_points} points")
    
    # Update points using the denormalized counter system
    update_user_points_counter(test_user_id, points_delta)
    logging.info(f"Updated points for {test_user_id} by +{points_delta}")
    
    # Verify the update
    user_doc = user_ref.get()
    leaderboard_doc = db.collection('leaderboard').document(test_user_id).get()
    
    if user_doc.exists and leaderboard_doc.exists:
        user_points = user_doc.to_dict().get('totalPoints', 0)
        leaderboard_points = leaderboard_doc.to_dict().get('totalPoints', 0)
        
        if user_points == leaderboard_points:
            logging.info(f"✓ Data consistency verified: user={user_points}, leaderboard={leaderboard_points}")
        else:
            logging.error(f"✗ Data inconsistency: user={user_points}, leaderboard={leaderboard_points}")
    else:
        logging.error("Test documents not found")

def test_leaderboard_stats():
    """Test leaderboard statistics functionality."""
    logging.info("Testing leaderboard statistics...")
    
    stats = get_leaderboard_stats()
    logging.info(f"Leaderboard stats: {stats}")
    
    if stats and 'totalUsers' in stats:
        logging.info(f"✓ Total users: {stats['totalUsers']}")
    else:
        logging.warning("Could not retrieve leaderboard stats")

def test_batch_update():
    """Test batch update functionality."""
    logging.info("Testing batch leaderboard update...")
    
    try:
        batch_update_leaderboard_entries()
        logging.info("✓ Batch update completed successfully")
    except Exception as e:
        logging.error(f"Batch update failed: {e}")

def cost_savings_analysis():
    """Analyze the cost savings from denormalized counters."""
    logging.info("Performing cost savings analysis...")
    
    # Original O(n) operations in gamification.py:
    # - base_query.count().get()[0][0].value (line 58)
    # - Multiple where(...).count() operations (lines 70, 71, 92, 93, 111, 112, etc.)
    
    # Each count() operation costs 1 document read per document counted
    # With denormalization, we replace these with:
    # - Single read from leaderboard_stats (1 read)
    # - Optimized queries on leaderboard collection
    
    # Estimated savings per leaderboard request:
    original_cost_per_request = "O(n) document reads (n = number of users)"
    new_cost_per_request = "O(1) for stats + O(page_size) for leaderboard entries"
    
    logging.info(f"Original cost per leaderboard request: {original_cost_per_request}")
    logging.info(f"New cost per leaderboard request: {new_cost_per_request}")
    
    # For a typical leaderboard with 1000 users:
    n_users = 1000
    page_size = 20
    
    original_reads = n_users * 3  # Multiple count operations per request
    new_reads = 1 + page_size  # stats + leaderboard entries
    
    savings_percent = ((original_reads - new_reads) / original_reads) * 100
    
    logging.info(f"Estimated reads for 1000 users:")
    logging.info(f"  - Original: {original_reads} reads per request")
    logging.info(f"  - New: {new_reads} reads per request")
    logging.info(f"  - Savings: {savings_percent:.1f}% reduction in reads")

if __name__ == "__main__":
    logging.info("Testing Denormalized Leaderboard Counters")
    logging.info("=" * 50)
    
    # Run tests
    test_points_update()
    test_leaderboard_stats()
    test_batch_update()
    cost_savings_analysis()
    
    logging.info("Test completed. Use 'python migrate_leaderboard.py' to initialize production data.")