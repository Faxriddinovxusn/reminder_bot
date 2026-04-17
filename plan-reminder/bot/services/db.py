from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from dotenv import load_dotenv
import os
import logging
from typing import Optional

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("MONGODB_DB", "plan_reminder")

client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None

def _validate_uri() -> None:
    if not MONGODB_URI:
        raise RuntimeError("MONGODB_URI is not set in the environment")

async def connect() -> None:
    global client, db
    try:
        _validate_uri()
        client = AsyncIOMotorClient(MONGODB_URI)
        db = client[DB_NAME]
        logging.info("Connected to MongoDB database '%s'", DB_NAME)
    except Exception as e:
        logging.exception("Failed to connect to MongoDB: %s", e)
        raise

def get_db() -> AsyncIOMotorDatabase:
    if db is None:
        raise RuntimeError("MongoDB not connected. Call connect() first.")
    return db

async def close() -> None:
    global client
    try:
        if client:
            client.close()
            client = None
    except Exception as e:
        logging.exception("Error closing MongoDB client: %s", e)
