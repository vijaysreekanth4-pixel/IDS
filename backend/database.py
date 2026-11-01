import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASS = os.getenv("MYSQL_PASSWORD", "")
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_NAME = os.getenv("MYSQL_DB", "ieas_db")

Base = declarative_base()
engine = None
SessionLocal = None

def _init_db():
    global engine, SessionLocal
    # 1. Try MySQL first
    try:
        import pymysql
        conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, connect_timeout=3)
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
        conn.commit()
        conn.close()
        
        SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}"
        engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        print(f"[DB] Connected to MySQL database '{DB_NAME}' successfully.")
        return
    except Exception as e:
        print(f"[DB] MySQL not available ({e}).")

    # 2. Seamless fallback to SQLite so auth and history work everywhere (e.g. Render / No-XAMPP)
    try:
        sqlite_path = os.path.join(os.path.dirname(__file__), "ieas_local.db")
        SQLITE_URL = f"sqlite:///{sqlite_path}"
        engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        print(f"[DB] Using SQLite fallback database at '{sqlite_path}'. All Auth and History will work.")
    except Exception as e:
        print(f"[DB] SQLite fallback error: {e}")
        engine = None
        SessionLocal = None

# Initialize on import
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
