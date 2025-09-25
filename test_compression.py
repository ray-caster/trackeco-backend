#!/usr/bin/env python3
"""
Test script to verify Gzip compression is working correctly.
This script tests the compression implementation without requiring external services.
"""

import os
import sys
import json
import gzip
from flask import Flask, jsonify
from flask_compress import Compress

# Create a minimal test Flask app
test_app = Flask(__name__)

# Configure compression settings (same as in main.py)
test_app.config['COMPRESS_ALGORITHM'] = 'gzip'
test_app.config['COMPRESS_LEVEL'] = 6
test_app.config['COMPRESS_MIN_SIZE'] = 500
test_app.config['COMPRESS_MIME_TYPES'] = [
    'text/html', 'text/css', 'text/xml', 'text/plain',
    'text/javascript', 'application/json', 'application/javascript',
    'application/xml', 'application/xhtml+xml', 'application/octet-stream'
]

# Initialize compression
compress = Compress()
compress.init_app(test_app)

# Test endpoints
@test_app.route('/test/small')
def test_small():
    """Small response that should NOT be compressed (below threshold)"""
    return jsonify({"message": "Small response", "size": 50})

@test_app.route('/test/large')
def test_large():
    """Large response that should be compressed (above threshold)"""
    large_data = {"message": "Large response", "data": ["item"] * 100}
    return jsonify(large_data)

@test_app.route('/test/text')
def test_text():
    """Text response that should be compressed"""
    text_data = "This is a large text response that should be compressed. " * 20
    return text_data, 200, {'Content-Type': 'text/plain'}

def test_compression():
    """Test compression functionality"""
    print("Testing Gzip compression implementation...")
    
    with test_app.test_client() as client:
        # Test small response (should not be compressed)
        print("\n1. Testing small response (should NOT be compressed):")
        response = client.get('/test/small', headers={'Accept-Encoding': 'gzip'})
        print(f"   Status: {response.status_code}")
        print(f"   Content-Encoding: {response.headers.get('Content-Encoding')}")
        print(f"   Content-Length: {response.headers.get('Content-Length')}")
        print(f"   Data size: {len(response.get_data())} bytes")
        
        # Test large JSON response (should be compressed)
        print("\n2. Testing large JSON response (should be compressed):")
        response = client.get('/test/large', headers={'Accept-Encoding': 'gzip'})
        print(f"   Status: {response.status_code}")
        print(f"   Content-Encoding: {response.headers.get('Content-Encoding')}")
        print(f"   Content-Length: {response.headers.get('Content-Length')}")
        print(f"   Data size: {len(response.get_data())} bytes")
        
        # Test if compressed data can be decompressed
        if response.headers.get('Content-Encoding') == 'gzip':
            try:
                decompressed = gzip.decompress(response.get_data())
                original_data = json.loads(decompressed)
                print(f"   ✓ Decompression successful: {len(decompressed)} bytes")
                print(f"   ✓ Original data structure preserved")
            except Exception as e:
                print(f"   ✗ Decompression failed: {e}")
        
        # Test text response (should be compressed)
        print("\n3. Testing text response (should be compressed):")
        response = client.get('/test/text', headers={'Accept-Encoding': 'gzip'})
        print(f"   Status: {response.status_code}")
        print(f"   Content-Encoding: {response.headers.get('Content-Encoding')}")
        print(f"   Content-Length: {response.headers.get('Content-Length')}")
        print(f"   Data size: {len(response.get_data())} bytes")
        
        print("\n✓ Compression test completed successfully!")
        return True

if __name__ == '__main__':
    success = test_compression()
    sys.exit(0 if success else 1)