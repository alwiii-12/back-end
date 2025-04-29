from fastapi import FastAPI, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import SessionLocal, engine
from models import QAResult, Base
from datetime import datetime
import pandas as pd
import os
import csv

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/upload/")
async def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    df = pd.read_excel(file.file)
    results = []
    tolerance = 2.0

    for _, row in df.iterrows():
        try:
            date = pd.to_datetime(row["Date"]).date()
            variation = float(row["Variation"])
            status = "Pass" if abs(variation) <= tolerance else "Fail"

            db_result = QAResult(date=date, variation=variation, status=status)
            db.add(db_result)
            results.append({"Date": date, "Variation": variation, "Status": status})
        except Exception:
            continue

    db.commit()

    os.makedirs("saved_results", exist_ok=True)
    with open("saved_results/qa_output.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Variation", "Status"])
        writer.writeheader()
        writer.writerows(results)

    return {"results": results}

@app.get("/download_saved_csv/")
def download_saved_csv():
    file_path = "saved_results/qa_output.csv"
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return {"csv": f.read()}
    return {"error": "No saved CSV found"}
