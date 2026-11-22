"""Narrow interpretation of the decoder rejection actually recorded by box2-m.

No generic 400 recovery: only this complete structured decoder response, before
any output, is eligible. Attribution to a newly offered tool image is separate.
"""
import json
import re

class MediaInputRejected(ValueError):
    media_rejected = True

def is_media_rejection(status, body):
    if status != 400:
        return False
    try:
        error = json.loads(body)['error']
    except (ValueError, KeyError, TypeError):
        return False
    return (isinstance(error, dict) and error.get('code') == 400
            and error.get('type') == 'invalid_request_error'
            and error.get('message') == 'Failed to load image or audio file')

def window_media_rejection(row):
    if int(row.get('stream_chunks') or 0):
        return False
    match = re.fullmatch(r'(?:RuntimeError: )?openai server HTTP (\d+): (\{.*\})', str(row.get('error') or ''))
    return bool(match and is_media_rejection(int(match[1]), match[2]))
