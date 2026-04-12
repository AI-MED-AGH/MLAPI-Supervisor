from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from dotenv import load_dotenv
import os

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///:memory:")

engine = create_engine(DATABASE_URL, pool_recycle=180, pool_pre_ping=True, pool_size=5)
 
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
