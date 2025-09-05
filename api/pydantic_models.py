from pydantic import BaseModel, EmailStr

# --- AUTHENTICATION & ONBOARDING ---
class AuthRequest(BaseModel):
    email: EmailStr
    password: str

class VerifyRequest(BaseModel):
    email: EmailStr
    code: str

class ResendCodeRequest(BaseModel):
    email: EmailStr

class GoogleAuthRequest(BaseModel):
    id_token: str

class OnboardingProfile(BaseModel):
    displayName: str
    username: str

class OnboardingSurvey(BaseModel):
    source: str
    motivation: str
    wasteType: str
    eventInterest: bool

class OnboardingReferral(BaseModel):
    referralCode: str | None = None
    contactHashes: list[str] | None = None

# --- CORE UPLOAD FLOW ---
class InitiateUploadRequest(BaseModel):
    upload_id: str
    filename: str
    fcm_token: str | None = None

class UploadCompleteRequest(BaseModel):
    upload_id: str

# --- SOCIAL & FRIENDS ---
class FriendRequest(BaseModel):
    targetUserId: str

class FriendResponseRequest(BaseModel):
    requesterUserId: str
    
class ContactHashesRequest(BaseModel):
    hashes: list[str]

# --- GAMIFICATION ---
class TeamUpRequest(BaseModel):
    challengeId: str
    inviteeIds: list[str]

class FcmTokenUpdateRequest(BaseModel):
    fcmToken: str

class AvatarUploadRequest(BaseModel):
    contentType: str
    fileExtension: str