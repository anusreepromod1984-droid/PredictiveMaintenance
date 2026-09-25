"""
PostgreSQL Database Connection Manager for Predictive Maintenance System.
Prisma-style ?schema=public is stripped — psycopg2/SQLAlchemy reject that query param.
"""

import os
from typing import List, Dict, Any, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.config import settings


def _psycopg_url(url: str) -> str:
    if not url:
        return url
    if "postgres:5432" in url and not os.path.exists("/.dockerenv"):
        # docker-compose publishes Postgres on host port 5433
        url = url.replace("postgres:5432", "localhost:5433")
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    kept = [part for part in query.split("&") if part and not part.lower().startswith("schema=")]
    return f"{base}?{'&'.join(kept)}" if kept else base


DATABASE_URL = _psycopg_url(settings.get_database_url())
DATABASE_URL_LOCAL = DATABASE_URL

engine = create_engine(
    DATABASE_URL_LOCAL,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 2}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def fetch_latest_telemetry_from_db(machine_id: Optional[str] = None, limit: int = 1) -> List[Dict[str, Any]]:
    """
    Fetches the latest time-series telemetry records from public.telemetry_reading table.
    Matches exact camelCase column schema: machineId, imuAcceleration, tempMotor, tempCompressor, etc.
    """
    if machine_id:
        query = text("""
            SELECT 
                "machineId",
                "timestamp",
                "receivedAt",
                "imuAcceleration",
                "rpm",
                "tempMotor",
                "tempCompressor",
                "humidity",
                "emIr",
                "emIy",
                "emIb",
                "emVr",
                "emVy",
                "emVb",
                "emMachineLoad",
                "emVoltageImbalance",
                "emPower"
            FROM public.telemetry_reading 
            WHERE "machineId" = :machine_id 
            ORDER BY "timestamp" DESC 
            LIMIT :limit;
        """)
        params = {"machine_id": machine_id, "limit": limit}
    else:
        query = text("""
            SELECT 
                "machineId",
                "timestamp",
                "receivedAt",
                "imuAcceleration",
                "rpm",
                "tempMotor",
                "tempCompressor",
                "humidity",
                "emIr",
                "emIy",
                "emIb",
                "emVr",
                "emVy",
                "emVb",
                "emMachineLoad",
                "emVoltageImbalance",
                "emPower"
            FROM public.telemetry_reading 
            ORDER BY "timestamp" DESC 
            LIMIT :limit;
        """)
        params = {"limit": limit}

    try:
        with engine.connect() as conn:
            result = conn.execute(query, params)
            records = [dict(row._mapping) for row in result]
            return records
    except Exception as e:
        print(f"Error fetching telemetry from telemetry_reading table: {e}")
        return []


def test_db_connection() -> bool:
    """Tests connection to PostgreSQL server and queries telemetry_reading count."""
    try:
        with engine.connect() as conn:
            res = conn.execute(text('SELECT COUNT(*) FROM public.telemetry_reading;'))
            count = res.scalar()
            print(f"Successfully connected to PostgreSQL! Found {count} records in public.telemetry_reading.")
            return True
    except Exception as e:
        print(f"PostgreSQL Connection Test Error: {e}")
        return False


if __name__ == "__main__":
    test_db_connection()
