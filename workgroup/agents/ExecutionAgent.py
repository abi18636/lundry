"""
ExecutionAgent - Real vs Dry Run
"""
class ExecutionAgent:
    def __init__(self, settings, engine):
        self.settings = settings
        self.engine = engine
    def is_real(self):
        return not self.settings.effective_dry_run if hasattr(self.settings, 'effective_dry_run') else not self.settings.dry_run
    def status(self):
        return {
            "effective_dry_run": getattr(self.settings, 'effective_dry_run', self.settings.dry_run),
            "has_keys": bool(self.settings.ariax_api_key),
            "trading_ready": True,
            "mode": "real" if self.is_real() else "dry_run_simulated"
        }
