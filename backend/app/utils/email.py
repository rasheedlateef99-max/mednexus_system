"""
MedNexus Email Utility
-------------------------
Centralised email sending via Resend.

All application emails go through this module.
If the email provider changes in the future, only this file needs updating.
No other part of the system knows which provider is being used.

Current provider: Resend (https://resend.com)
Free tier: 3,000 emails/month — sufficient for development and early production.

Usage:
    from app.utils.email import send_staff_invitation_email

    await send_staff_invitation_email(
        to_email="doctor@hospital.com",
        to_name="Dr. Sarah Okafor",
        hospital_name="City General Hospital",
        inviter_name="Admin Tunde",
        invitation_token="abc123",
    )
"""

import resend
from app.config import settings

# Initialise the Resend client with the API key from .env
resend.api_key = settings.RESEND_API_KEY


async def send_staff_invitation_email(
    to_email: str,
    to_name: str,
    hospital_name: str,
    inviter_name: str,
    invitation_token: str,
    role: str,
) -> bool:
    """
    Sends a staff invitation email to a new team member.

    The email contains a link with the invitation token embedded.
    The recipient clicks the link, lands on the frontend, and sets
    up their password and profile.

    Args:
        to_email:         Recipient email address
        to_name:          Recipient full name (for personalisation)
        hospital_name:    Name of the hospital they are being invited to
        inviter_name:     Name of the admin who sent the invitation
        invitation_token: Secure token for the invitation link
        role:             Their assigned role (displayed in the email)

    Returns:
        True if sent successfully, False if sending failed.
        We return False rather than raising so that a failed email
        does not roll back the invitation record — the admin can
        resend manually.
    """
    # Build the invitation URL the recipient clicks
    # The frontend reads the token from the URL and calls the
    # accept-invitation endpoint
    invitation_url = (
        f"{settings.FRONTEND_URL}/accept-invitation?token={invitation_token}"
    )

    # Format the role for display (hospital_admin → Hospital Admin)
    display_role = role.replace("_", " ").title()

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
    </head>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">

        <div style="text-align: center; margin-bottom: 30px;">
            <h1 style="color: #2563eb; font-size: 24px; margin: 0;">MedNexus</h1>
            <p style="color: #666; margin: 5px 0 0;">Healthcare Platform</p>
        </div>

        <div style="background: #f8fafc; border-radius: 8px; padding: 30px; margin-bottom: 20px;">
            <h2 style="margin-top: 0; color: #1e293b;">You have been invited to join {hospital_name}</h2>

            <p>Hello {to_name},</p>

            <p>
                <strong>{inviter_name}</strong> has invited you to join
                <strong>{hospital_name}</strong> on MedNexus as a
                <strong>{display_role}</strong>.
            </p>

            <p>MedNexus is a digital healthcare platform that helps medical teams
            manage patients, appointments, records, and more — all in one place.</p>

            <div style="text-align: center; margin: 30px 0;">
                <a href="{invitation_url}"
                   style="background: #2563eb; color: white; padding: 14px 28px;
                          text-decoration: none; border-radius: 6px; font-size: 16px;
                          font-weight: bold; display: inline-block;">
                    Accept Invitation
                </a>
            </div>

            <p style="color: #666; font-size: 14px;">
                This invitation link expires in 72 hours.
                If you did not expect this invitation, you can safely ignore this email.
            </p>

            <p style="color: #666; font-size: 13px;">
                If the button above does not work, copy and paste this link into your browser:<br>
                <a href="{invitation_url}" style="color: #2563eb; word-break: break-all;">
                    {invitation_url}
                </a>
            </p>
        </div>

        <p style="color: #999; font-size: 12px; text-align: center;">
            © MedNexus — Healthcare Digital Infrastructure<br>
            This is an automated message. Please do not reply to this email.
        </p>

    </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": f"MedNexus <{settings.FROM_EMAIL}>",
            "to": [to_email],
            "subject": f"You have been invited to join {hospital_name} on MedNexus",
            "html": html_content,
        })
        return True

    except Exception as e:
        # Log the error but do not raise — the invitation record
        # was already created. The admin can resend the email.
        print(f"[Email] Failed to send invitation to {to_email}: {e}")
        return False


async def send_hospital_approved_email(
    to_email: str,
    to_name: str,
    hospital_name: str,
) -> bool:
    """
    Notifies a hospital administrator that their application has been approved.

    Sent by the platform when a platform admin approves a hospital application.

    Args:
        to_email:      Hospital admin's email
        to_name:       Hospital admin's name
        hospital_name: Name of the approved hospital

    Returns:
        True if sent successfully, False otherwise
    """
    login_url = f"{settings.FRONTEND_URL}/login"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">

        <div style="text-align: center; margin-bottom: 30px;">
            <h1 style="color: #2563eb;">MedNexus</h1>
        </div>

        <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 30px; margin-bottom: 20px;">
            <h2 style="color: #166534; margin-top: 0;">
                🎉 Your hospital has been approved!
            </h2>

            <p>Hello {to_name},</p>

            <p>
                We are pleased to inform you that <strong>{hospital_name}</strong>
                has been approved on MedNexus. Your hospital workspace is now active
                and ready to use.
            </p>

            <p>You can now:</p>
            <ul>
                <li>Set up your departments</li>
                <li>Invite your medical and administrative staff</li>
                <li>Register and manage patients</li>
                <li>Schedule appointments</li>
                <li>And much more</li>
            </ul>

            <div style="text-align: center; margin: 30px 0;">
                <a href="{login_url}"
                   style="background: #16a34a; color: white; padding: 14px 28px;
                          text-decoration: none; border-radius: 6px; font-size: 16px;
                          font-weight: bold; display: inline-block;">
                    Go to Your Dashboard
                </a>
            </div>
        </div>

    </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": f"MedNexus <{settings.FROM_EMAIL}>",
            "to": [to_email],
            "subject": f"Your hospital {hospital_name} is approved on MedNexus",
            "html": html_content,
        })
        return True
    except Exception as e:
        print(f"[Email] Failed to send approval email to {to_email}: {e}")
        return False