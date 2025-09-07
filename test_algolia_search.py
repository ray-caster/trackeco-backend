# FILE: /home/trackeco/app/test_algolia_search.py

import os
import logging
from dotenv import load_dotenv
from algoliasearch.search.client import SearchClientSync
import json
# --- SETUP ---
# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables from the .env file in the current directory
load_dotenv()

# --- Load and Verify Configuration ---
ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID")
ALGOLIA_ADMIN_API_KEY = os.environ.get("ALGOLIA_ADMIN_API_KEY")
ALGOLIA_INDEX_NAME = os.environ.get("ALGOLIA_INDEX_NAME")

# --- 1. DEBUGGING: Print the loaded configuration ---
print("--- Algolia Configuration ---")
logging.info(f"Attempting to connect with App ID: {ALGOLIA_APP_ID}")
logging.info(f"Using Index Name: {ALGOLIA_INDEX_NAME}")
# Print only a portion of the key for security
logging.info(f"Admin Key starts with: {ALGOLIA_ADMIN_API_KEY[:4] if ALGOLIA_ADMIN_API_KEY else 'None'}")
print("-----------------------------\n")

# Check if credentials are set at all
if not all([ALGOLIA_APP_ID, ALGOLIA_ADMIN_API_KEY]):
    logging.error("FATAL: Algolia App ID or Admin API Key is not set. Please check your .env file.")
    exit()

try:
    # --- 2. INITIALIZE THE CLIENT ---
    logging.info("Initializing Algolia SearchClientSync...")
    client = SearchClientSync(ALGOLIA_APP_ID, ALGOLIA_ADMIN_API_KEY)
    logging.info("Client initialized successfully.")

    # --- 3. PERFORM THE SEARCH ---
    search_query = "genie"
    logging.info(f"Performing search for query: '{search_query}'...")
    
    # --- THIS IS THE FIX ---
    # The search method takes a single dictionary containing a list of requests.
    results = client.search({
        "requests": [
            {
                "indexName": ALGOLIA_INDEX_NAME,
                "query": search_query,
                "hitsPerPage": 5,
            }
        ]
    })
    # -----------------------
    
    # --- 4. PRINT THE RESULTS ---
    logging.info("Search request completed successfully!")
    print("\n--- SEARCH RESULTS ---")
    results_dict = json.loads(results.to_json())
    
    # Now we can safely use .get() to extract the nested data.
    hits = results_dict.get('results', [{}])[0].get('hits', [])
    # -----------------------------
    print(results_dict)
    print(results)
    if hits:
        print(f"Found {len(hits)} result(s):")
        for hit in hits:
            print(f"  - objectID: {hit.get('objectID')}, displayName: {hit.get('displayName')}, points: {hit.get('totalPoints')}")
    else:
        print("Query returned 0 results.")
    print("----------------------\n")
    logging.info("✅ Test script finished successfully!")

except Exception as e:
    # (The error catching part remains the same)
    logging.error("❌ An error occurred during the test!")
    logging.error(f"Error Type: {type(e).__name__}")
    logging.error(f"Error Details: {e}", exc_info=True)

except Exception as e:
    # (The error catching part remains the same)
    logging.error("❌ An error occurred during the test!")
    logging.error(f"Error Type: {type(e).__name__}")
    logging.error(f"Error Details: {e}", exc_info=True)
    print("\n--- DEBUGGING HELP ---")
    print("If you see 'Invalid Application-ID or API-Key', double-check your .env file.")
    print("If you see 'Index users does not exist', check the index name in the Algolia dashboard.")
    print("If you see a 'ConnectionError' or 'Timeout', your server may have a firewall blocking outbound requests to Algolia.")
    print("----------------------\n")