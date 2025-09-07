from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Union

# --- AUTH & ONBOARDING ---
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
    referralCode: Optional[str] = None
    contactHashes: Optional[List[str]] = None

# --- CORE UPLOAD & USER MANAGEMENT ---
class InitiateUploadRequest(BaseModel):
    upload_id: str
    filename: str
    fcm_token: Optional[str] = None

class UploadCompleteRequest(BaseModel):
    upload_id: str # Corresponds to the ID from the initiateUpload response

class AvatarUploadRequest(BaseModel):
    contentType: str
    fileExtension: str

class AvatarUploadCompleteRequest(BaseModel):
    gcsPath: str
    
class FcmTokenUpdateRequest(BaseModel):
    fcmToken: str

class UsernameCheckRequest(BaseModel):
    username: str
    
class UserSearchResponse(BaseModel):
    userId: str
    displayName: Optional[str] = None
    username: Optional[str] = None
    avatarUrl: Optional[str] = None

# --- SOCIAL & FRIENDS ---
class FriendRequest(BaseModel):
    targetUserId: str

class FriendResponseRequest(BaseModel):
    requesterUserId: str
    
class ContactHashesRequest(BaseModel):
    hashes: List[str]

# --- GAMIFICATION ---
class TeamUpRequest(BaseModel):
    challengeId: str
    inviteeIds: List[str]

class TeamChallengeInvitation(BaseModel):
    teamChallengeId: str
    description: str
    hostDisplayName: str

# --- CANONICAL USER & RESPONSE MODELS ---

# This is now the single, canonical model for any user summary
class UserSummary(BaseModel):
    rank: int
    displayName: Optional[str] = "Anonymous"
    userId: str
    totalPoints: int = 0
    avatarUrl: Optional[str] = None
    isCurrentUser: bool = False
    docId: Optional[str] = None

# The response for the /v2/leaderboard endpoint
class V2LeaderboardResponse(BaseModel):
    leaderboardPage: List[UserSummary]
    myRank: Optional[UserSummary] = None
    totalUsers: int = 0

# The response for the /users/{userId}/profile endpoint
class PublicProfileResponse(BaseModel):
    userId: str
    displayName: Optional[str] = "Anonymous"
    username: Optional[str] = None
    avatarUrl: Optional[str] = None
    totalPoints: int = 0
    currentStreak: int = 0

# The main, unified response for the /users/me endpoint
class ProfileResponse(BaseModel):
    userId: str
    displayName: Optional[str] = None
    username: Optional[str] = None
    avatarUrl: Optional[str] = None
    totalPoints: int
    currentStreak: int
    maxStreak: int
    referralCode: Optional[str] = None
    onboardingComplete: bool
    onboardingStep: int
    completedChallengeIds: List[str] = []
    challengeProgress: dict = {}
    activeTeamChallenges: List[str] = []
    teamChallengeInvitations: List[TeamChallengeInvitation] = []
    friends: List[UserSummary] = []
    sentRequests: List[UserSummary] = []
    receivedRequests: List[UserSummary] = []
    