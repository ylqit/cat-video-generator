"""Ark Responses 视觉审核使用的严格结构化输出 Schema。"""

KEYFRAME_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "identityOk": {"type": "boolean"},
        "styleOk": {"type": "boolean"},
        "worldStateOk": {"type": "boolean"},
        "sceneTopologyOk": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "violations": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "identityOk",
        "styleOk",
        "worldStateOk",
        "sceneTopologyOk",
        "confidence",
        "violations",
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
