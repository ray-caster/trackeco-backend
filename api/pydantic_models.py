from pydantic import BaseModel, EmailStr

class AuthRequest(BaseModel): email: EmailStr; password: str
class VerifyRequest(BaseModel): email: EmailStr; code: str
class ResendCodeRequest(BaseModel): email: EmailStr
class GoogleAuthRequest(BaseModel): id_token: str
class InitiateUploadRequest(BaseModel): upload_id: str; filename: str; fcm_token: str | None = None
class UploadCompleteRequest(BaseModel): upload_id: str
class OnboardingProfile(BaseModel): displayName: str; username: str
class OnboardingSurvey(BaseModel): source: str; motivation: str; wasteType: str; eventInterest: bool
class OnboardingReferral(BaseModel): referralCode: str | None = None; contactHashes: list[str] | None = None
class FriendRequest(BaseModel): targetUserId: str
class FriendResponseRequest(BaseModel): requesterUserId: str
class ContactHashesRequest(BaseModel): hashes: list[str]
class TeamUpRequest(BaseModel): challengeId: str; inviteeIds: list[str]