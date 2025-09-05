import os
from io import BytesIO
from google.cloud import firestore, storage
from PIL import Image

# Initialize clients globally for re-use between function invocations
storage_client = storage.Client()
db = firestore.Client()

def resize_and_store_image(event, context):
    """
    Triggered by a new file upload to GCS. It resizes the image,
    saves it to a public folder, and updates the user's profile in Firestore.
    """
    file_data = event
    bucket_name = file_data['bucket']
    file_name = file_data['name']

    # Ensure we only process files from the original avatars folder
    if not file_name.startswith('avatars_original/'):
        return

    # Extract the user ID from the filename (e.g., 'avatars_original/some_user_id.jpg')
    user_id = os.path.splitext(os.path.basename(file_name))[0]
    
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_name)
    
    # Download the image into memory
    image_bytes = blob.download_as_bytes()
    
    # Open the image with Pillow
    with Image.open(BytesIO(image_bytes)) as img:
        # Resize to a 256x256 thumbnail
        img.thumbnail((256, 256))
        
        # Convert to WebP format in memory
        output_buffer = BytesIO()
        img.save(output_buffer, format='WEBP', quality=85)
        output_buffer.seek(0)

    # Define the path for the new processed avatar
    processed_blob_name = f"avatars_processed/{user_id}.webp"
    dest_blob = bucket.blob(processed_blob_name)
    
    # Upload the resized image
    dest_blob.upload_from_file(output_buffer, content_type='image/webp')
    
    # Make the processed image publicly readable
    dest_blob.make_public()
    
    # Update the user's document in Firestore with the new public URL
    user_ref = db.collection('users').document(user_id)
    user_ref.update({'avatarUrl': dest_blob.public_url})
    
    print(f"Successfully resized and updated avatar for user: {user_id}")

    # Optional: Delete the original high-resolution image to save costs
    blob.delete()