from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

class ConversationState(str, Enum):
    OPENING = "opening"
    DISCOVERY = "discovery"
    QUALIFICATION = "qualification"
    BOOKING = "booking"
    ESCALATION = "escalation"
    CONFIRMED = "confirmed"
    WAITING = "waiting"      # Lead sending low-content spam
    CLOSED = "closed"        # Conversation permanently ended

class LeadCreate(BaseModel):
    name: str
    phone: str
    company: str

class BANTScoreDetail(BaseModel):
    score: int = Field(ge=0, le=10)
    evidence: str

class BANTScores(BaseModel):
    budget: BANTScoreDetail
    authority: BANTScoreDetail
    need: BANTScoreDetail
    timeline: BANTScoreDetail
    overall_score: int = Field(ge=0, le=10)
    buying_signals: List[str]
    recommended_action: str

class MessageLog(BaseModel):
    phone: str
    direction: str
    body: str
    state: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class SessionData(BaseModel):
    state: ConversationState = ConversationState.OPENING
    history: List[Dict[str, str]] = []
    bant_scores: Optional[Dict[str, Any]] = None
    lead_data: Dict[str, Any] = {}
    turn_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated: datetime = Field(default_factory=datetime.utcnow)

class WebhookPayload(BaseModel):
    SmsMessageSid: Optional[str] = Field(None, alias="MessageSid")
    NumMedia: Optional[str] = None
    ProfileName: Optional[str] = None
    SmsSid: Optional[str] = None
    WaitUntil: Optional[str] = None
    To: str
    From: str
    MessageSid: str
    ApiVersion: Optional[str] = None
    Body: str
    AccountSid: Optional[str] = None
