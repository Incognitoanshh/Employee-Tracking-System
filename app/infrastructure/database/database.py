
from sqlalchemy import create_engine

from sqlalchemy.orm import sessionmaker

from app.core.config.settings import Settings


engine = create_engine(
    Settings.DATABASE_URL,
    echo=False
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)