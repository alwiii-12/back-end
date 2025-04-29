from sqlalchemy import Column, Integer, String, Float, Date
from database import Base

class QAResult(Base):
    __tablename__ = "qa_results"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False)
    variation = Column(Float, nullable=False)
    status = Column(String, nullable=False)
