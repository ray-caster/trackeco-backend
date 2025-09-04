import os
import logging
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

def send_verification_email(recipient_email, verification_code):
    """
    Sends the verification code to the user using the Brevo API.

    This function reads credentials from environment variables, constructs an
    HTML email, and sends it using the Brevo SDK.

    Args:
        recipient_email (str): The email address to send the verification to.
        verification_code (str): The 6-digit code to include in the email.
        
    Returns:
        bool: True if the email was sent successfully, False otherwise.
    """
    # 1. Load credentials securely from environment variables.
    api_key = os.environ.get("BREVO_API_KEY")
    sender_name = "TrackEco Verification"
    sender_email = os.environ.get("VERIFIED_SENDER_EMAIL")

    if not api_key or not sender_email:
        logging.error("CRITICAL: BREVO_API_KEY or VERIFIED_SENDER_EMAIL is not set in the environment. Cannot send email.")
        return False

    # 2. Configure the Brevo API client.
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = api_key
    
    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))

    # 3. Define the sender, recipient, and email content.
    sender = sib_api_v3_sdk.SendSmtpEmailSender(name=sender_name, email=sender_email)
    to = [sib_api_v3_sdk.SendSmtpEmailTo(email=recipient_email)]

    # 4. Create the HTML template for the email body.
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f7f6; }}
            .container {{ max-width: 600px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); overflow: hidden; }}
            .header {{ background-color: #000033; color: #ffffff; padding: 24px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 24px; }}
            .content {{ padding: 32px; text-align: center; color: #333333; line-height: 1.6; }}
            .code-box {{ font-size: 36px; font-weight: bold; letter-spacing: 8px; margin: 24px 0; padding: 16px; background-color: #f2f2f2; border: 1px dashed #cccccc; border-radius: 5px; color: #000033; }}
            .footer {{ font-size: 12px; color: #888888; text-align: center; padding: 24px; background-color: #f9fafb; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Welcome to TrackEco!</h1>
            </div>
            <div class="content">
                <p>Here is your verification code. Enter this code in the app to activate your account and start making an impact.</p>
                <div class="code-box">
                    {verification_code}
                </div>
                <p>This code will expire in 15 minutes.</p>
            </div>
            <div class="footer">
                <p>If you did not request this email, you can safely ignore it.</p>
            </div>
        </div>
    </body>
    </html>
    """

    # 5. Assemble the final email object.
    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=to,
        sender=sender,
        subject="Your TrackEco Verification Code",
        html_content=html_content
    )

    # 6. Send the email and handle potential errors.
    try:
        api_response = api_instance.send_transac_email(send_smtp_email)
        logging.info(f"Successfully sent verification email to {recipient_email}. Brevo Message ID: {api_response.message_id}")
        return True
    except ApiException as e:
        # Log the full error body from the API for better debugging
        logging.error(f"Failed to send verification email to {recipient_email} using Brevo. Status: {e.status}, Body: {e.body}")
        return False