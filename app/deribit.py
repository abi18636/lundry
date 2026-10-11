"""Shim for backward compatibility - Deribit -> AriaX"""
from app.ariax import AriaXClient, AriaXAPIError

# Aliases for old code
DeribitClient = AriaXClient
DeribitAPIError = AriaXAPIError
