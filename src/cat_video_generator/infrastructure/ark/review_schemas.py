"""Ark Responses 视觉审核使用的严格结构化输出 Schema。"""

STORYBOARD_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "identityOk": {"type": "boolean"},
        "styleOk": {"type": "boolean"},
        "actionSequenceOk": {"type": "boolean"},
        "continuityOk": {"type": "boolean"},
        "endingOk": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "violations": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "identityOk",
        "styleOk",
        "actionSequenceOk",
        "continuityOk",
        "endingOk",
        "confidence",
        "violations",
        "warnings",
        "evidence",
    ],
    "additionalProperties": False,
}

VIDEO_DIAGNOSTIC_SCHEMA = {
    "type": "object",
    "properties": {
        "identityOk": {"type": "boolean"},
        "styleOk": {"type": "boolean"},
        "worldContinuityOk": {"type": "boolean"},
        "narrativeOrderOk": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "violations": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "identityOk",
        "styleOk",
        "worldContinuityOk",
        "narrativeOrderOk",
        "confidence",
        "violations",
        "evidence",
    ],
    "additionalProperties": False,
}
