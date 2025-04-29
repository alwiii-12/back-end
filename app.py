from fastapi import FastAPI, UploadFile, File, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import SessionLocal, engine
from models import Base, OutputVariation
import openpyxl
import io
import csv

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI()

# Allow frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can restrict to your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    workbook = openpyxl.load_workbook(io.BytesIO(contents))
    sheet = workbook.active

    results = []

    for row in sheet.iter_rows(min_row=2, values_only=True):
        date, raw_variation = row[0], row[1]

        if date is None or raw_variation is None:
            continue

        try:
            variation = float(raw_variation)
        except ValueError:
            continue

        # Assign status based on tolerance
        if abs(variation) <= 2:
            status = "Pass"
        elif abs(variation) <= 3:
            status = "Warning"
        else:
            status = "Fail"

        # Save to DB
        entry = OutputVariation(date=str(date), variation=variation, status=status)
        db.add(entry)

        results.append({
            "date": str(date),
            "variation": round(variation, 1),
            "status": status
        })

    db.commit()
    return JSONResponse(content={"results": results})

@app.post("/download_csv")
async def download_csv(data: dict):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Variation", "Status"])
    for item in data.get("results", []):
        writer.writerow([item["date"], item["variation"], item["status"]])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]),
                             media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=evaluation_results.csv"})

@app.get("/download_saved_csv")
def download_saved_csv(db: Session = Depends(get_db)):
    entries = db.query(OutputVariation).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Variation", "Status"])
    for item in entries:
        writer.writerow([item.date, item.variation, item.status])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]),
                             media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=saved_results.csv"})
