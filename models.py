from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

class User(db.Model):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), unique=True, nullable=False)
    username = Column(String(80), unique=True, nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    xp = Column(Integer, default=0)
    points = Column(Integer, default=0)
    rupiah_balance = Column(Float, default=0.0)
    streak = Column(Integer, default=0)
    last_disposal_date = Column(Date)
    last_rupiah_bonus_date = Column(Date)
    has_completed_first_disposal = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<User {self.user_id}>'

class Disposal(db.Model):
    __tablename__ = 'disposals'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    waste_category = Column(String(100), nullable=False)
    waste_sub_type = Column(String(100), nullable=False)
    points_awarded = Column(Integer, nullable=False)
    
    def __repr__(self):
        return f'<Disposal {self.user_id} - {self.waste_category}>'

class DailyDisposalLog(db.Model):
    __tablename__ = 'daily_disposal_log'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False)
    date = Column(Date, nullable=False)
    waste_sub_type = Column(String(100), nullable=False)
    
    def __repr__(self):
        return f'<DailyLog {self.user_id} - {self.date} - {self.waste_sub_type}>'

class Challenge(db.Model):
    __tablename__ = 'challenges'
    
    id = Column(Integer, primary_key=True)
    challenge_id = Column(String(100), unique=True, nullable=False)
    challenge_type = Column(String(50), nullable=False)  # COUNT, VARIETY, HOTSPOT
    category = Column(String(100))  # For category-specific challenges
    goal = Column(Integer, nullable=False)
    reward = Column(Integer, nullable=False)
    description = Column(Text, nullable=False)
    
    def __repr__(self):
        return f'<Challenge {self.challenge_id}>'

class UserChallengeProgress(db.Model):
    __tablename__ = 'user_challenge_progress'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False)
    challenge_id = Column(String(100), nullable=False)
    current_progress = Column(Integer, default=0)
    is_completed = Column(Boolean, default=False)
    assigned_date = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<UserChallengeProgress {self.user_id} - {self.challenge_id}>'

class Hotspot(db.Model):
    __tablename__ = 'hotspots'
    
    id = Column(Integer, primary_key=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    intensity = Column(Float, nullable=False)  # 0.0 to 1.0
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    
    def __repr__(self):
        return f'<Hotspot {self.latitude}, {self.longitude}>'

class UserSkill(db.Model):
    __tablename__ = 'user_skills'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False)
    skill_type = Column(String(50), nullable=False)  # waste_detective, prevention_expert, impact_champion
    current_xp = Column(Integer, default=0)
    level = Column(Integer, default=1)
    
    def __repr__(self):
        return f'<UserSkill {self.user_id} - {self.skill_type} Level {self.level}>'
    
    @property
    def xp_required_for_next_level(self):
        """Calculate XP required for next level"""
        return self.level * 100  # 100, 200, 300, 400, 500 XP per level
    
    @property
    def progress_to_next_level(self):
        """Calculate progress percentage to next level"""
        xp_for_current = (self.level - 1) * 100
        xp_for_next = self.level * 100
        progress_xp = self.current_xp - xp_for_current
        return min(100, (progress_xp / 100) * 100)

class WasteType(db.Model):
    __tablename__ = 'waste_types'
    
    id = Column(Integer, primary_key=True)
    category = Column(String(50), nullable=False)  # plastic, paper, glass, metal, etc.
    sub_type = Column(String(100), nullable=False)
    rarity = Column(String(20), default='common')  # common, uncommon, rare, epic
    discovery_xp = Column(Integer, default=10)
    
    def __repr__(self):
        return f'<WasteType {self.category} - {self.sub_type}>'

class UserDiscovery(db.Model):
    __tablename__ = 'user_discoveries'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False)
    waste_type_id = Column(Integer, ForeignKey('waste_types.id'), nullable=False)
    discovered_at = Column(DateTime, default=datetime.utcnow)
    
    waste_type = relationship('WasteType', backref='discoveries')
    
    def __repr__(self):
        return f'<UserDiscovery {self.user_id} - {self.waste_type_id}>'

class DailyMission(db.Model):
    __tablename__ = 'daily_missions'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False)
    mission_date = Column(Date, default=datetime.utcnow().date)
    mission_type = Column(String(50), nullable=False)  # 'plastic_variety', 'count_challenge', etc.
    description = Column(Text, nullable=False)
    goal = Column(Integer, nullable=False)
    current_progress = Column(Integer, default=0)
    reward_points = Column(Integer, default=50)
    is_completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<DailyMission {self.user_id} - {self.mission_date}>'
    
    @property
    def time_remaining(self):
        """Calculate time remaining for this daily mission"""
        tomorrow = datetime.combine(self.mission_date + timedelta(days=1), datetime.min.time())
        now = datetime.utcnow()
        if now >= tomorrow:
            return "Expired"
        
        remaining = tomorrow - now
        hours = remaining.seconds // 3600
        minutes = (remaining.seconds % 3600) // 60
        return f"{hours}h {minutes}m"
