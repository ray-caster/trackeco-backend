import os
from io import BytesIO
from google.cloud import storage
from PIL import Image

# Initialize clients once per instance
storage_client = storage.Client()
THUMB_SIZE = (128, 128)
PROFILE_SIZE = (512, 512)

def resize_avatar(data, context):
    """
    Cloud Function triggered by a Cloud Storage event.
    Resizes a newly uploaded user avatar into multiple sizes.
    """
    bucket_name = data['bucket']
    filename = data['name']
    
    print(f"Processing file: {filename} from bucket: {bucket_name}.")

    # --- Pre-computation checks to avoid errors and recursive triggers ---
    # Ensure the event is not from a file deletion
    if context.event_type == 'google.storage.object.delete':
        print(f"Ignoring delete event for file: {filename}")
        return

    # Ensure we don't process already processed files to prevent loops
    if 'processed/avatars/' in filename:
        print(f"Ignoring already processed file: {filename}")
        return

    # Ensure the file is in the expected directory for original uploads
    parts = filename.split('/')
    if len(parts) < 2 or parts[0] != 'avatars_original':
        print(f"Ignoring file not in 'avatars_original/' directory: {filename}")
        return
        
    user_id = parts[1]
    print(f"Identified user ID: {user_id}")

    # --- Image Processing ---
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(filename)
    
    # Check if the blob exists before proceeding
    if not blob.exists():
        print(f"Blob {filename} no longer exists. Exiting.")
        return

    try:
        with BytesIO(blob.download_as_bytes()) as in_mem_file:
            image = Image.open(in_mem_file)
            
            # Convert to RGB to handle formats like PNG with alpha channels gracefully
            if image.mode not in ('RGB', 'L'):
                image = image.convert('RGB')
            
            # 1. Generate and upload Thumbnail
            thumb = image.copy()
            thumb.thumbnail(THUMB_SIZE)
            thumb_blob_name = f"processed/avatars/{user_id}/thumb.jpg"
            thumb_blob = bucket.blob(thumb_blob_name)
            with BytesIO() as out_mem_file:
                thumb.save(out_mem_file, format='JPEG', quality=85)
                thumb_blob.upload_from_string(out_mem_file.getvalue(), content_type='image/jpeg')
                thumb_blob.make_public()
            print(f"Generated and uploaded thumbnail to {thumb_blob_name}")

            # 2. Generate and upload Profile Size
            profile_img = image.copy()
            profile_img.thumbnail(PROFILE_SIZE)
            profile_blob_name = f"processed/avatars/{user_id}/profile.jpg"
            profile_blob = bucket.blob(profile_blob_name)
            with BytesIO() as out_mem_file:
                profile_img.save(out_mem_file, format='JPEG', quality=90)
                profile_blob.upload_from_string(out_mem_file.getvalue(), content_type='image/jpeg')
                profile_blob.make_public()
            print(f"Generated and uploaded profile image to {profile_blob_name}")
                
        # 3. Clean up the original large file
        blob.delete()
        print(f"Successfully processed and deleted original avatar: {filename}")

    except Exception as e:
        print(f"ERROR: Failed to process image {filename}. Error: {e}")
        failed_blob_name = f"failed/avatars/{filename}"
        bucket.rename_blob(blob, new_name=failed_blob_name)