"""Garmin activity-type vocabulary and Chinese labels.

The Garmin Connect CSV export ("导出为csv文献") writes Chinese type names, while
the Connect API returns English ``typeKey`` values.  Both are mapped onto one
canonical key so a record synced online deduplicates against the same record
synced from a CSV file.
"""

from __future__ import annotations

# Garmin Connect API typeKey -> canonical key.
_TYPE_KEY_ALIASES: dict[str, str] = {
    "running": "running",
    "treadmill_running": "treadmill_running",
    "indoor_running": "indoor_running",
    "trail_running": "trail_running",
    "track_running": "track_running",
    "obstacle_run": "obstacle_run",
    "ultra_run": "ultra_run",
    "cycling": "cycling",
    "road_biking": "road_biking",
    "mountain_biking": "mountain_biking",
    "gravel_cycling": "gravel_cycling",
    "indoor_cycling": "indoor_cycling",
    "virtual_ride": "virtual_ride",
    "commuting": "commuting",
    "walking": "walking",
    "hiking": "hiking",
    "swimming": "swimming",
    "lap_swimming": "lap_swimming",
    "open_water_swimming": "open_water_swimming",
    "strength_training": "strength_training",
    "indoor_cardio": "indoor_cardio",
    "cardio_training": "cardio_training",
    "elliptical": "elliptical",
    "stair_climbing": "stair_climbing",
    "rowing": "rowing",
    "indoor_rowing": "indoor_rowing",
    "yoga": "yoga",
    "pilates": "pilates",
    "breathwork": "breathwork",
    "meditation": "meditation",
    "hiit": "hiit",
    "jump_rope": "jump_rope",
    "bouldering": "bouldering",
    "floor_climbing": "floor_climbing",
    "multi_sport": "multi_sport",
    "skiing": "skiing",
    "snowboarding": "snowboarding",
    "tennis": "tennis",
    "badminton": "badminton",
    "basketball": "basketball",
    "soccer": "soccer",
    "table_tennis": "table_tennis",
    "golf": "golf",
    "other": "other",
}

# Chinese type names produced by the Garmin Connect CN CSV export.
_TYPE_LABEL_ALIASES: dict[str, str] = {
    "跑步": "running",
    "跑步机": "treadmill_running",
    "室内跑步": "indoor_running",
    "越野跑": "trail_running",
    "田径跑": "track_running",
    "障碍赛跑": "obstacle_run",
    "超马": "ultra_run",
    "骑行": "cycling",
    "自行车": "cycling",
    "公路骑行": "road_biking",
    "山地骑行": "mountain_biking",
    "砾石骑行": "gravel_cycling",
    "室内骑行": "indoor_cycling",
    "虚拟骑行": "virtual_ride",
    "通勤": "commuting",
    "步行": "walking",
    "徒步": "hiking",
    "游泳": "swimming",
    "泳池游泳": "lap_swimming",
    "公开水域游泳": "open_water_swimming",
    "力量训练": "strength_training",
    "室内有氧": "indoor_cardio",
    "有氧运动": "cardio_training",
    "椭圆机": "elliptical",
    "爬楼": "stair_climbing",
    "划船": "rowing",
    "室内划船": "indoor_rowing",
    "瑜伽": "yoga",
    "普拉提": "pilates",
    "呼吸训练": "breathwork",
    "冥想": "meditation",
    "高强度间歇训练": "hiit",
    "跳绳": "jump_rope",
    "抱石": "bouldering",
    "攀岩": "floor_climbing",
    "多项运动": "multi_sport",
    "滑雪": "skiing",
    "单板滑雪": "snowboarding",
    "网球": "tennis",
    "羽毛球": "badminton",
    "篮球": "basketball",
    "足球": "soccer",
    "乒乓球": "table_tennis",
    "高尔夫": "golf",
    "其他": "other",
}

# canonical key -> Chinese label used in the diary line.
_CANONICAL_LABELS: dict[str, str] = {
    "running": "跑步",
    "treadmill_running": "跑步机",
    "indoor_running": "室内跑步",
    "trail_running": "越野跑",
    "track_running": "田径跑",
    "obstacle_run": "障碍赛跑",
    "ultra_run": "超长跑",
    "cycling": "骑行",
    "road_biking": "公路骑行",
    "mountain_biking": "山地骑行",
    "gravel_cycling": "砾石骑行",
    "indoor_cycling": "室内骑行",
    "virtual_ride": "虚拟骑行",
    "commuting": "通勤",
    "walking": "步行",
    "hiking": "徒步",
    "swimming": "游泳",
    "lap_swimming": "泳池游泳",
    "open_water_swimming": "公开水域游泳",
    "strength_training": "力量训练",
    "indoor_cardio": "室内有氧",
    "cardio_training": "有氧运动",
    "elliptical": "椭圆机",
    "stair_climbing": "爬楼",
    "rowing": "划船",
    "indoor_rowing": "室内划船",
    "yoga": "瑜伽",
    "pilates": "普拉提",
    "breathwork": "呼吸训练",
    "meditation": "冥想",
    "hiit": "高强度间歇",
    "jump_rope": "跳绳",
    "bouldering": "抱石",
    "floor_climbing": "攀岩",
    "multi_sport": "多项运动",
    "skiing": "滑雪",
    "snowboarding": "单板滑雪",
    "tennis": "网球",
    "badminton": "羽毛球",
    "basketball": "篮球",
    "soccer": "足球",
    "table_tennis": "乒乓球",
    "golf": "高尔夫",
    "other": "运动",
}


def canonical_type_key(raw: str) -> str:
    """Normalise an API ``typeKey`` or a CSV type label to a canonical key."""
    text = (raw or "").strip()
    if not text:
        return "other"
    lowered = text.lower()
    if lowered in _TYPE_KEY_ALIASES:
        return _TYPE_KEY_ALIASES[lowered]
    if text in _TYPE_LABEL_ALIASES:
        return _TYPE_LABEL_ALIASES[text]
    # Fall back to a slug so unknown types still group consistently.
    return lowered.replace(" ", "_")


def is_known_type(raw: str) -> bool:
    """True when ``raw`` is itself an activity-type name rather than a title.

    Garmin sometimes names an activity after its own type ("有氧运动" for an
    indoor-cardio activity).  Repeating that in the diary line is noise.
    """
    text = (raw or "").strip().lower()
    if not text:
        return False
    return text in _TYPE_KEY_ALIASES or (raw or "").strip() in _TYPE_LABEL_ALIASES


def type_label(raw: str) -> str:
    """Human-facing Chinese label for a raw type key or CSV type label."""
    canonical = canonical_type_key(raw)
    if canonical in _CANONICAL_LABELS:
        return _CANONICAL_LABELS[canonical]
    text = (raw or "").strip()
    return text or "运动"
