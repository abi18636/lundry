"""
Telegram Module - Commands
Independent: Only command parsing
"""
def parse_command(text: str):
    """Parse /command and argument"""
    if not text or not text.startswith("/"):
        return None, ""
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    return cmd, arg
