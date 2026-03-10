# -*- coding: utf-8 -*-
"""
Единая политика лицензирования VignetteCloud.

Этот модуль должен быть единственным источником правды для:
- названий тарифов
- списка приложений по тарифам
"""

PLAN_APPS = {
    "demo": set(),
    "basic": {
        "VignetteNamer",
        "FaceSorter",
        "VignetteCropper",
        "AcneRemover",
        "VignetteFiller",
        "SpreadLayout",
        "IndividualFiller",
        "ExportCovers",
    },
    "pro": {
        "VignetteConstructorPro",
        "AcneRemover",
        "VignetteCropper",
        "FaceSorter",
        "SpreadConstructor",
        "VignetteNamer",
        "VignetteFiller",
        "SpreadLayout",
        "IndividualFiller",
        "ExportCovers",
    },
}

PLAN_LABELS = {
    "demo": "Demo",
    "basic": "Basic",
    "pro": "Pro",
}