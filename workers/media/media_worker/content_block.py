"""Provider-reported policy blocks are not transient request failures."""

BLOCKED_TEXT = "차단된 영역입니다"


class ContentBlockedError(RuntimeError):
    def __init__(self, reason, provider='Gemini'):
        self.reason = reason
        self.provider = provider
        super().__init__(f"{provider} content blocked ({reason})")


def check_content_block(data):
    reason = (data.get("promptFeedback") or {}).get("blockReason")
    if reason and reason != "BLOCK_REASON_UNSPECIFIED":
        raise ContentBlockedError(reason if reason in {
            "SAFETY", "OTHER", "BLOCKLIST", "PROHIBITED_CONTENT", "IMAGE_SAFETY"} else "OTHER")
    for candidate in data.get("candidates") or []:
        reason = candidate.get("finishReason")
        if reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}:
            raise ContentBlockedError(reason)


def block_details(error):
    blocks = getattr(error, "content_blocks", [])
    if isinstance(error, ContentBlockedError):
        blocks = [{"reason": error.reason, "provider": error.provider}]
    providers = sorted({block.get('provider', 'Gemini') for block in blocks})
    return {"kind": "content_blocked", "provider": ', '.join(providers), "blocks": blocks} if blocks else None
