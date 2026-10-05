import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# Fallback to local default XAMPP/WAMP MySQL server
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASS = os.getenv("MYSQL_PASSWORD", "")
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_NAME = os.getenv("MYSQL_DB", "ieas_db")

Base = declarative_base()
engine = None
SessionLocal = None

def _init_db():
    global engine, SessionLocal
    try:
        import pymysql
        # First, ensure database exists
        conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, connect_timeout=3)
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
        conn.commit()
        conn.close()
        print(f"[DB] Database '{DB_NAME}' is ready.")
        
        # Create engine
        SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}"
        engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        print("[DB] SQLAlchemy engine created successfully.")
        
    except Exception as e:
        print(f"[DB] MySQL not available: {e}")
        print("[DB] Running in NO-DB mode. Analysis history will NOT be saved.")
        engine = None
        SessionLocal = None

# Try to connect at module load time
_init_db()

def get_db():
    if not SessionLocal:
        yield None
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
