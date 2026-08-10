"""Ark Responses 视觉审核使用的严格结构化输出 Schema。"""

IMAGE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "identityOk": {"type": "boolean"},
        "styleOk": {"type": "boolean"},
        "appearanceOk": {"type": "boolean"},
        "compositionOk": {"type": "boolean"},
        "criticalPropsOk": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "violations": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "identityOk",
        "styleOk",
        "appearanceOk",
        "compositionOk",
        "criticalPropsOk",
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
        "criticalPropsOk": {"type": "boolean"},
        "narrativeOrderOk": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "violations": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "identityOk",
        "styleOk",
        "criticalPropsOk",
        "narrativeOrderOk",
        "confidence",
        "violations",
        "evidence",
    ],
    "additionalProperties": False,
}
