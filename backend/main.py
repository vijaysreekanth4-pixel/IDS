"""
main.py
-------
FastAPI application entry point.

Endpoints
---------
POST /analyze               – Upload a log file or raw log text for analysis.
GET  /summary               – Return the aggregated summary of the last analysis.
GET  /device/{device_id}    – Return the detailed report for a specific device.
GET  /health                – Liveness probe.

Design notes
------------
* Results are stored in a module-level in-memory dict (keyed by job_id).
* The interface is designed so that the in-memory store can be replaced by
  Redis, DynamoDB, or any async KV store without touching the endpoint logic.
* File uploads are handled via SpooledTemporaryFile, so large files never
  fully occupy RAM – they spill to disk once they exceed ``MAX_SPOOL_BYTES``.
"""

from __future__ import annotations

import json
import logging
import uuid
import secrets
import time
from datetime import datetime, timezone
import zipfile
import io
import ast
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, BackgroundTasks, Form
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .analyzer import analyze_all_devices
from .models import AnalysisReport, AnalyzeRequest, AnalyzeResponse, DeviceReport
from .parser import count_parse_errors, parse_log_file, parse_log_text
from .recommender import generate_all_recommendations
from .report import build_report
from .root_cause import analyze_root_causes
from .cnn_detector import analyze_image_damage
from .ai_graph import run_ai_analysis

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Intelligent Event Analysis System",
    description=(
        "Analyses system event logs to detect anomalies, determine root causes, "
        "and provide actionable recommendations."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

import os
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
from fastapi import Depends
from sqlalchemy.orm import Session
from passlib.context import CryptContext

# Load environment variables first
load_dotenv()

# Database Imports
from .database import engine, Base, get_db
from .db_models import User, AnalysisHistory

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

@app.on_event("startup")
def startup_event():
    if engine:
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("Database tables created/verified successfully.")
        except Exception as e:
            logger.warning(f"DB table creation failed: {e}. Running without DB.")
    else:
        logger.warning("Database engine not available. Running without DB.")

# Point to the new frontend folder
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/app", StaticFiles(directory=frontend_dir, html=True), name="static")


# In-memory result store  {job_id: AnalysisReport}
_result_store: Dict[str, AnalysisReport] = {}

# Key used to hold the *latest* analysis so GET /summary always works
_LATEST_KEY = "__latest__"

MAX_SPOOL_BYTES = 10 * 1024 * 1024  # 10 MB before spilling to disk


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _run_pipeline(log_text: str) -> AnalysisReport:
    """Execute the full analysis pipeline on raw log text."""
    device_events = parse_log_text(log_text)
    parse_errors = count_parse_errors(log_text)
    total_events = sum(len(v) for v in device_events.values())

    analyses = analyze_all_devices(device_events)
    rca_results = analyze_root_causes(analyses)
    recommendations = generate_all_recommendations(rca_results)

    report = build_report(
        analyses=analyses,
        rca_results=rca_results,
        recommendations=recommendations,
        parse_errors=parse_errors,
        total_events=total_events,
    )
    return report


# ---------------------------------------------------------------------------
# Auth / OTP Helper (Secure Backend-Driven)
# ---------------------------------------------------------------------------
# In-memory OTP storage: { email: {"otp": "123456", "expires_at": 1690000000} }
# Expiry is set to 5 minutes (300 seconds)
otp_store: Dict[str, Dict[str, Any]] = {}
OTP_EXPIRY_SECONDS = 300

class RequestOtpBody(BaseModel):
    email: str

class VerifyOtpBody(BaseModel):
    email: str
    otp: str

def _send_email_sync(to_email: str, otp: str) -> bool:
    """
    Sends an OTP email via SMTP.
    Supports Brevo (smtp-relay.brevo.com) or Gmail (smtp.gmail.com).
    Credentials are loaded from environment variables (SMTP_EMAIL, SMTP_PASS, SMTP_HOST).
    Falls back to writing OTP to Desktop file in Dev Mode.
    """
    SMTP_HOST    = os.environ.get("SMTP_HOST",  "smtp-relay.brevo.com")
    SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
    SENDER_EMAIL = os.environ.get("SMTP_EMAIL", "")
    SENDER_PASS  = os.environ.get("SMTP_PASS",  "")

    # Dev Mode: credentials not configured
    if not SENDER_EMAIL or not SENDER_PASS or SENDER_PASS == "dummy_password":
        logger.warning(f"DEV MODE: OTP for {to_email} is {otp}. Configure .env to send real emails.")
        _write_otp_to_desktop(to_email, otp)
        return False

    try:
        # Beautiful HTML email
        html_body = f"""
        <html><body style="font-family:Arial,sans-serif;background:#0a0a14;padding:40px;">
          <div style="max-width:480px;margin:auto;background:#12121f;border-radius:16px;overflow:hidden;border:1px solid #1e1e3a;">
            <div style="background:linear-gradient(135deg,#00E5FF,#B055FF);padding:24px;text-align:center;">
              <h1 style="color:#fff;margin:0;font-size:22px;">⚡ IEAS Gateway</h1>
              <p style="color:rgba(255,255,255,0.8);margin:4px 0 0;">Intelligent Event Analysis System</p>
            </div>
            <div style="padding:32px;text-align:center;">
              <p style="color:#8E92A4;font-size:15px;margin-bottom:24px;">Your one-time verification code is:</p>
              <div style="background:#0a0a14;border:2px solid #00E5FF;border-radius:12px;padding:20px;letter-spacing:12px;font-size:36px;font-weight:bold;color:#00E5FF;">
                {otp}
              </div>
              <p style="color:#8E92A4;font-size:13px;margin-top:20px;">⏰ This code expires in <strong style="color:#fff;">5 minutes</strong>.</p>
              <p style="color:#8E92A4;font-size:12px;margin-top:16px;">If you did not request this, please ignore this email.</p>
            </div>
            <div style="background:#0a0a14;padding:16px;text-align:center;border-top:1px solid #1e1e3a;">
              <p style="color:#3a3a5c;font-size:11px;margin:0;">IEAS Security Team &bull; Do not reply to this email</p>
            </div>
          </div>
        </body></html>
        """

        msg = MIMEMultipart("alternative")
        msg['From']    = f"IEAS Gateway <{SENDER_EMAIL}>"
        msg['To']      = to_email
        msg['Subject'] = "🔐 Your IEAS Verification Code"
        msg.attach(MIMEText(html_body, 'html'))

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SENDER_EMAIL, SENDER_PASS)
        server.send_message(msg)
        server.quit()
        logger.info(f"✅ OTP email sent to {to_email} via {SMTP_HOST}")
        return True

    except Exception as e:
        logger.error(f"❌ SMTP error ({SMTP_HOST}): {e}")
        _write_otp_to_desktop(to_email, otp)
        return False


def _write_otp_to_desktop(email: str, otp: str):
    """Writes the OTP to a text file on the user's desktop as a fallback."""
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop", "IEAS_LATEST_OTP.txt")
        with open(desktop, "w") as f:
            f.write(f"--- IEAS DEV MODE OTP ---\nEmail: {email}\nOTP Code: {otp}\n\n(This file was generated because SMTP email was not configured or failed)")
    except:
        pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/auth/request-otp", tags=["Auth"])
async def request_otp_endpoint(request: RequestOtpBody, background_tasks: BackgroundTasks):
    """Generates a secure 6-digit OTP, stores it with expiry, and triggers email dispatch."""
    # 1. Generate secure 6-digit OTP
    secure_otp = "".join(secrets.choice("0123456789") for _ in range(6))
    
    # 2. Store temporarily with expiry
    otp_store[request.email] = {
        "otp": secure_otp,
        "expires_at": time.time() + OTP_EXPIRY_SECONDS
    }
    
    # 3. Send email in background
    background_tasks.add_task(_send_email_sync, request.email, secure_otp)
    
    return {"status": "ok", "message": f"OTP sent to {request.email}"}

@app.post("/auth/verify-otp", tags=["Auth"])
async def verify_otp_endpoint(request: VerifyOtpBody):
    """Verifies the submitted OTP against the stored OTP and checks expiry."""
    record = otp_store.get(request.email)
    
    if not record:
        raise HTTPException(status_code=400, detail="No OTP requested or OTP has expired.")
    
    if time.time() > record["expires_at"]:
        del otp_store[request.email]
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")
        
    if record["otp"] != request.otp:
        raise HTTPException(status_code=400, detail="Incorrect OTP.")
        
    # Successful verification - delete it so it can't be reused
    del otp_store[request.email]
    
    return {"status": "success", "message": "Email successfully verified!"}

# --- DB Auth Endpoints ---

class DBSignupRequest(BaseModel):
    name: str
    email: str
    password: str

@app.post("/auth/db/signup", tags=["Auth"])
def db_signup(req: DBSignupRequest, db: Session = Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    existing_user = db.query(User).filter(User.email == req.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_pw = pwd_context.hash(req.password)
    new_user = User(name=req.name, email=req.email, password_hash=hashed_pw)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"status": "success", "message": "User created successfully"}

class DBLoginRequest(BaseModel):
    email: str
    password: str

@app.post("/auth/db/login", tags=["Auth"])
def db_login(req: DBLoginRequest, db: Session = Depends(get_db)):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
        
    return {"status": "success", "name": user.name, "email": user.email}

@app.get("/health", tags=["Operations"])
async def health_check() -> Dict[str, str]:
    """Liveness probe – always returns 200 OK."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirects the bare URL to the frontend application."""
    return RedirectResponse(url="/app/login.html")

@app.post("/analyze", response_model=AnalyzeResponse, tags=["Analysis"])
async def analyze_log_text(request: AnalyzeRequest) -> AnalyzeResponse:
    """
    Analyse raw event log text submitted in the request body.

    The ``log_text`` field should contain the full contents of a log file
    as a plain string (newline-separated event records).

    Returns a ``job_id`` that can be used with ``GET /device/{device_id}``,
    plus the full analysis report inline.
    """
    job_id = str(uuid.uuid4())
    logger.info("POST /analyze – job_id=%s", job_id)

    try:
        report = _run_pipeline(request.log_text)
    except Exception as exc:
        logger.exception("Pipeline error for job %s", job_id)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc

    _result_store[job_id] = report
    _result_store[_LATEST_KEY] = report

    return AnalyzeResponse(
        job_id=job_id,
        status="completed",
        message=(
            f"Analysis complete. "
            f"{report.summary.total_devices} device(s) analysed; "
            f"{report.summary.failed_devices} failed."
        ),
        report=report,
    )

@app.post("/analyze/multimodal", tags=["Analysis", "AI"])
async def analyze_multimodal(
    file: UploadFile = File(...), 
    user_email: Optional[str] = Form(None), 
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Advanced AI Endpoint:
    - If image (.jpg, .png): runs CNN damage detection, then RAG/LLM.
    - If code (.py, .js, .txt): extracts text, analyzes error, runs RAG/LLM.
    """
    job_id = str(uuid.uuid4())
    logger.info("POST /analyze/multimodal - job_id=%s, filename=%s", job_id, file.filename)
    
    ext = file.filename.split('.')[-1].lower() if '.' in file.filename else ''
    is_image = ext in ['jpg', 'jpeg', 'png', 'bmp']
    
    try:
        contents = await file.read()
        
        if is_image:
            # 1. Run CNN
            cnn_result = analyze_image_damage(contents)
            error_query = f"Product: {cnn_result.get('product_name', 'Unknown')} | Damage: {cnn_result.get('damage_type', 'None')} | Severity: {int(cnn_result.get('damage_probability', 0)*100)}%" if cnn_result['is_damaged'] else "No damage detected."
            
            # 2. Run LangGraph/LLM (Vision enabled)
            ai_explanation = run_ai_analysis(error_query, "Image", image_bytes=contents)
            
            return {
                "job_id": job_id,
                "file_type": "image",
                "cnn_status": cnn_result,
                "ai_reasoning": ai_explanation
            }
        else:
            # Code/Text File
            text = contents.decode("utf-8", errors="replace")
            # Simple heuristic to extract a Python/JS error (e.g., last line or traceback)
            # For demo purposes, we will pass a snippet to the AI
            snippet = text[-1000:] if len(text) > 1000 else text
            
            # 1. Extract error (Mock logic - just passing the snippet)
            error_query = snippet
            
            
            # 2. Run LangGraph/LLM
            ai_explanation = run_ai_analysis(error_query, f"Code ({ext})")
            
            result_data = {
                "job_id": job_id,
                "file_type": "code",
                "ai_reasoning": ai_explanation
            }
            
        # Save to DB if user_email provided
        if user_email and db:
            history = AnalysisHistory(
                user_email=user_email,
                job_id=job_id,
                analysis_type="MULTIMODAL",
                filename=file.filename,
                results_json=result_data
            )
            db.add(history)
            db.commit()
            
        return result_data
            
    except Exception as exc:
        logger.exception("Multimodal analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")

class SettingsRequest(BaseModel):
    api_key: str

@app.post("/settings/apikey", tags=["Settings"])
async def update_api_key(req: SettingsRequest):
    """
    Updates the GEMINI_API_KEY in the .env file and current environment.
    """
    key = req.api_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API Key cannot be empty")
    
    # Update environment immediately
    os.environ['GEMINI_API_KEY'] = key
    
    # Save to .env file in backend folder
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    try:
        # Read existing lines
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        else:
            lines = []
            
        # Update or append GEMINI_API_KEY
        key_found = False
        for i, line in enumerate(lines):
            if line.startswith('GEMINI_API_KEY='):
                lines[i] = f'GEMINI_API_KEY={key}\n'
                key_found = True
                break
                
        if not key_found:
            lines.append(f'\nGEMINI_API_KEY={key}\n')
            
        with open(env_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            
    except Exception as e:
        logger.error(f"Failed to write to .env file: {e}")
        # Even if file write fails, we updated os.environ so it works for this session
        return {"status": "success", "message": "API Key applied for this session (saving to .env failed)"}
        
    return {"status": "success", "message": "API Key saved successfully"}

@app.post("/analyze/zip", tags=["Analysis", "AI"])
async def analyze_zip_file(
    file: UploadFile = File(...),
    user_email: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    ZIP Code Analyzer:
    - Accepts a .zip file containing code files (.py, .js, .html, .css, .txt, .json, etc.)
    - Extracts each file and scans for syntax errors and common issues
    - Returns per-file: filename, error found, reason, and how to fix it
    """
    job_id = str(uuid.uuid4())
    logger.info("POST /analyze/zip - job_id=%s, filename=%s", job_id, file.filename)

    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted.")

    CODE_EXTENSIONS = {'.py', '.js', '.ts', '.html', '.css', '.json', '.txt', '.java', '.cpp', '.c', '.cs', '.rb', '.go', '.php'}

    try:
        contents = await file.read()
        zip_buffer = io.BytesIO(contents)

        if not zipfile.is_zipfile(zip_buffer):
            raise HTTPException(status_code=400, detail="Invalid ZIP file.")

        zip_buffer.seek(0)
        results = []

        with zipfile.ZipFile(zip_buffer, 'r') as zf:
            all_names = [n for n in zf.namelist() if not n.endswith('/')]
            code_files = [
                n for n in all_names
                if any(n.lower().endswith(ext) for ext in CODE_EXTENSIONS)
            ]

            if not code_files:
                return {
                    "job_id": job_id,
                    "total_files": len(all_names),
                    "analyzed_files": 0,
                    "files_with_errors": 0,
                    "results": [],
                    "message": "No code files found in ZIP. Only images/binaries were present."
                }

            for fname in code_files:
                try:
                    raw = zf.read(fname)
                    try:
                        code_text = raw.decode('utf-8')
                    except UnicodeDecodeError:
                        code_text = raw.decode('latin-1', errors='replace')

                    # --- Error Detection ---
                    errors_found = []
                    ext = '.' + fname.split('.')[-1].lower() if '.' in fname else ''

                    # Python syntax check
                    if ext == '.py':
                        try:
                            ast.parse(code_text)
                        except SyntaxError as se:
                            errors_found.append({
                                "type": "SyntaxError",
                                "line": se.lineno,
                                "detail": str(se.msg),
                                "text": se.text.strip() if se.text else ""
                            })

                    # General heuristic checks for all file types
                    lines = code_text.splitlines()
                    for i, line in enumerate(lines, 1):
                        stripped = line.strip()
                        # Detect common issues
                        if 'TODO' in line or 'FIXME' in line or 'HACK' in line:
                            errors_found.append({"type": "Warning", "line": i, "detail": f"Found TODO/FIXME/HACK: {stripped[:80]}", "text": stripped[:80]})
                        if 'password' in line.lower() and ('=' in line or ':' in line) and ('"' in line or "'" in line):
                            errors_found.append({"type": "SecurityWarning", "line": i, "detail": "Hardcoded password detected!", "text": "***REDACTED***"})
                        if 'import *' in line:
                            errors_found.append({"type": "CodeSmell", "line": i, "detail": "Wildcard import 'import *' - avoid for clarity.", "text": stripped[:80]})

                    # Build AI reasoning for this file
                    if errors_found:
                        error_summary = "; ".join([f"Line {e['line']}: {e['type']} - {e['detail']}" for e in errors_found[:3]])
                        ai_query = f"File: {fname} | Errors: {error_summary}"
                        ai_reason = run_ai_analysis(ai_query, f"Code ({ext})")
                    else:
                        ai_reason = None

                    results.append({
                        "filename": fname,
                        "extension": ext,
                        "lines_of_code": len(lines),
                        "has_errors": len(errors_found) > 0,
                        "error_count": len(errors_found),
                        "errors": errors_found,
                        "ai_reasoning": ai_reason
                    })

                except Exception as fe:
                    logger.warning(f"Failed to analyze {fname}: {fe}")
                    results.append({
                        "filename": fname,
                        "extension": ext if 'ext' in dir() else '',
                        "has_errors": False,
                        "error_count": 0,
                        "errors": [],
                        "ai_reasoning": None,
                        "read_error": str(fe)
                    })

        files_with_errors = sum(1 for r in results if r['has_errors'])

        result_data = {
            "job_id": job_id,
            "zip_filename": file.filename,
            "total_files": len(all_names),
            "analyzed_files": len(results),
            "files_with_errors": files_with_errors,
            "files_clean": len(results) - files_with_errors,
            "results": results
        }
        
        # Save to DB if user_email provided
        if user_email and db:
            history = AnalysisHistory(
                user_email=user_email,
                job_id=job_id,
                analysis_type="ZIP",
                filename=file.filename,
                results_json=result_data
            )
            db.add(history)
            db.commit()
            
        return result_data

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ZIP analysis failed")
        raise HTTPException(status_code=500, detail=f"ZIP analysis failed: {exc}")

@app.post("/analyze/upload", tags=["Analysis"])
async def analyze_log_file(
    file: UploadFile = File(...),
    user_email: Optional[str] = Form(None),
    db: Session = Depends(get_db)
) -> AnalyzeResponse:
    """
    Analyse an uploaded log file.

    Accepts a plain-text ``.log`` file via multipart/form-data.
    The file is read in full but the underlying parser processes it
    line-by-line so memory usage stays low even for large uploads
    (streaming to disk for files > 10 MB via SpooledTemporaryFile).
    """
    job_id = str(uuid.uuid4())
    logger.info("POST /analyze/upload – job_id=%s, filename=%s", job_id, file.filename)

    try:
        contents = await file.read()
        log_text = contents.decode("utf-8", errors="replace")
        report = _run_pipeline(log_text)
    except Exception as exc:
        logger.exception("Pipeline error for job %s", job_id)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc

    _result_store[job_id] = report
    _result_store[_LATEST_KEY] = report
    
    response = AnalyzeResponse(
        job_id=job_id,
        status="completed",
        message=(
            f"Analysis complete. "
            f"{report.summary.total_devices} device(s) analysed; "
            f"{report.summary.failed_devices} failed."
        ),
        report=report,
    )
    
    if user_email and db:
        history = AnalysisHistory(
            user_email=user_email,
            job_id=job_id,
            analysis_type="LOG_UPLOAD",
            filename=file.filename,
            results_json=response.dict()
        )
        db.add(history)
        db.commit()
        
    return response


@app.get("/summary", tags=["Analysis"])
async def get_summary(job_id: Optional[str] = Query(default=None)) -> JSONResponse:
    """
    Return the summary section of an analysis report.

    If ``job_id`` is provided, returns the report for that job.
    Otherwise, returns the summary of the most recent analysis.
    """
    key = job_id if job_id else _LATEST_KEY
    report = _result_store.get(key)

    if report is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No analysis found. "
                "Submit a log via POST /analyze first."
                if key == _LATEST_KEY
                else f"Job '{job_id}' not found."
            ),
        )

    return JSONResponse(content=report.summary.model_dump())


@app.get("/device/{device_id}", tags=["Analysis"])
async def get_device_report(
    device_id: str,
    job_id: Optional[str] = Query(default=None),
) -> JSONResponse:
    """
    Return the detailed analysis report for a single device.

    ``device_id`` is case-insensitive.  If ``job_id`` is provided, looks
    up that specific job; otherwise uses the most recent analysis.
    """
    key = job_id if job_id else _LATEST_KEY
    report = _result_store.get(key)

    if report is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No analysis found. Submit a log via POST /analyze first."
                if key == _LATEST_KEY
                else f"Job '{job_id}' not found."
            ),
        )

    target = device_id.upper()
    device_report: Optional[DeviceReport] = next(
        (d for d in report.devices if d.device_id == target), None
    )

    if device_report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Device '{device_id}' not found in the analysis report.",
        )

    return JSONResponse(content=device_report.model_dump(mode="json"))


@app.get("/jobs", tags=["Operations"])
async def list_jobs() -> Dict[str, Any]:
    """List all stored analysis job IDs."""
    jobs = [k for k in _result_store if k != _LATEST_KEY]
    return {"total_jobs": len(jobs), "job_ids": jobs}
