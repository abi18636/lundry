"""
Backward compatibility shim - now uses AriaXClient
Deribit exchange completely removed per user request - history wiped
"""
from app.ariax import AriaXClient, AriaXAPIError, DeribitClient

# Alias for old imports
DeribitAPIError = AriaXAPIError
