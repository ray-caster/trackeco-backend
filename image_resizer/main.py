import os
from io import BytesIO
from google.cloud import firestore, storage
from PIL import Image

# Initialize clients globally. In a Cloud Function environment, these are reused
# between invocations for efficiency.
storage_client = storage.Client()
db = firestore.Client()

def resize_and_store_image(event, context):
    """
    This is a Google Cloud Function, triggered by a new file upload to Cloud Storage.
    It resizes the uploaded image, saves the thumbnail to a public folder,
    updates the user's profile in Firestore with the new public URL, and deletes the original.
    """
    # Get the file details from the trigger event
    file_data = event
    bucket_name = file_data['bucket']
    original_file_name = file_data['name']
    original_content_type = file_data.get('contentType', 'image/jpeg')

    # IMPORTANT: We only want this function to run for files in the 'avatars_original/' directory.
    # This prevents an infinite loop where resizing an image triggers the function again.
    if not original_file_name.startswith('avatars_original/'):
        print(f"Ignoring file '{original_file_name}' as it is not in the target directory.")
        return

    # Extract the user ID from the filename.
    # e.g., 'avatars_original/some_user_id.jpg' becomes 'some_user_id'
    user_id = os.path.splitext(os.path.basename(original_file_name))[0]
    
    bucket = storage_client.bucket(bucket_name)
    source_blob = bucket.blob(original_file_name)
    
    try:
        # Download the original, high-resolution image into memory
        image_bytes = source_blob.download_as_bytes()
        
        # Determine the output format based on the original file type
        if 'png' in original_content_type.lower():
            output_format = 'PNG'
            file_extension = 'png'
        else: # Default to JPEG for everything else
            output_format = 'JPEG'
            file_extension = 'jpg'

        with Image.open(BytesIO(image_bytes)) as img:
            # If the image has transparency (like a PNG) and we're saving as JPEG,
            # create a white background to prevent issues.
            if img.mode in ('RGBA', 'LA') and output_format == 'JPEG':
                background = Image.new(img.mode[:-1], img.size, (255, 255, 255))
                background.paste(img, img.getchannel('A'))
                img = background

            # Resize the image to a 256x256 thumbnail while maintaining aspect ratio
            img.thumbnail((256, 256))
            
            # Save the resized image to an in-memory buffer
            output_buffer = BytesIO()
            img.save(output_buffer, format=output_format, quality=85)
            output_buffer.seek(0)

        # Define the path for the new processed avatar with the correct extension
        processed_blob_name = f"avatars_processed/{user_id}.{file_extension}"
        dest_blob = bucket.blob(processed_blob_name)
        
        # Upload the resized image from memory to the 'avatars_processed/' folder
        dest_blob.upload_from_file(output_buffer, content_type=original_content_type)
        
        # Make the newly uploaded thumbnail publicly readable
        dest_blob.make_public()
        
        # Update the user's document in Firestore with the new public URL
        user_ref = db.collection('users').document(user_id)
        user_ref.update({'avatarUrl': dest_blob.public_url})
        
        print(f"Successfully resized and updated avatar for user: {user_id}")

        # Clean up the original high-resolution file to save storage costs
        source_blob.delete()

    except Exception as e:
        print(f"ERROR: Failed to process image for user {user_id}. Error: {e}")
        # In case of failure, you might want to move the original to a 'failed' folder
        # instead of just deleting it, for later inspection.