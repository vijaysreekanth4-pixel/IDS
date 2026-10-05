from sqlalchemy import Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.sql import func
from .database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100))
    email = Column(String(100), unique=True, index=True)
    password_hash = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class AnalysisHistory(Base):
    __tablename__ = "analysis_history"
    
    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String(100), index=True)
    job_id = Column(String(100), unique=True, index=True)
    analysis_type = Column(String(50)) # 'LOG', 'ZIP', 'MULTIMODAL'
    filename = Column(String(255))
    results_json = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
