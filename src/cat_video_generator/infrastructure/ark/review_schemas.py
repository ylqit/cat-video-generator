"""Ark Responses 视觉审核使用的严格结构化输出 Schema。"""

IMAGE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "identityOk": {"type": "boolean"},
        "styleOk": {"type": "boolean"},
        "appearanceOk": {"type": "boolean"},
        "compositionOk": {"type": "boolean"},
        "constraintsOk": {"type": "boolean"},
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
        "constraintsOk",
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
        "constraintsOk": {"type": "boolean"},
        "narrativeOrderOk": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "violations": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "object": {"type": "string"},
                    "observation": {"type": "string"},
                    "relationError": {"type": ["string", "null"]},
                },
                "required": ["timestamp", "object", "observation", "relationError"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "identityOk",
        "styleOk",
        "constraintsOk",
        "narrativeOrderOk",
        "confidence",
        "violations",
        "evidence",
    ],
    "additionalProperties": False,
}
