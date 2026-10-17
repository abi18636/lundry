"""
Execution Module - AriaX Client
Independent: Only exchange communication, no strategy
"""
# Re-export from original ariax.py for modularity
from app.ariax import AriaXClient, AriaXAsyncClient, AriaXAPIError
__all__ = ["AriaXClient", "AriaXAsyncClient", "AriaXAPIError"]
