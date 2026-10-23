"""Controlled vocabulary for metrics.

Cross-source comparison only works if every source is mapped onto the same
metric key and the same condition fields; free-text labels are the main reason
"矛盾" is usually just "口径不同".
"""

from __future__ import annotations

from typing import Dict, List, Optional

METRICS: Dict[str, Dict[str, object]] = {
    "price": {"label": "价格", "unit": "CNY", "kind": "number", "conditions": ["storage"]},
    "soc": {"label": "SoC", "unit": "", "kind": "text", "conditions": []},
    "screen_size": {"label": "屏幕尺寸", "unit": "inch", "kind": "number", "conditions": []},
    "screen_refresh": {"label": "刷新率", "unit": "Hz", "kind": "number", "conditions": []},
    "screen_brightness": {"label": "全屏激发亮度", "unit": "nit", "kind": "number", "conditions": ["mode"]},
    "screen_peak_brightness": {"label": "峰值亮度", "unit": "nit", "kind": "number", "conditions": []},
    "screen_manual_brightness": {"label": "手动最高亮度", "unit": "nit", "kind": "number", "conditions": []},
    "screen_brightness_ingame": {"label": "游戏内屏幕亮度", "unit": "nit", "kind": "number", "conditions": ["game"]},
    "battery": {"label": "电池容量", "unit": "mAh", "kind": "number", "conditions": []},
    "charge_power": {"label": "有线充电功率", "unit": "W", "kind": "number", "conditions": ["type"]},
    "charge_wireless_power": {"label": "无线充电功率", "unit": "W", "kind": "number", "conditions": []},
    "charge_time": {"label": "充满时间", "unit": "min", "kind": "number", "conditions": ["from_soc", "to_soc"]},
    "weight": {"label": "重量", "unit": "g", "kind": "number", "conditions": ["with_case"]},
    "thickness": {"label": "厚度", "unit": "mm", "kind": "number", "conditions": []},
    "battery_life": {"label": "亮屏续航", "unit": "h", "kind": "number", "conditions": ["brightness_nit", "network"]},
    "fps": {"label": "平均帧率", "unit": "fps", "kind": "number", "conditions": ["game", "resolution", "duration_min", "stat"]},
    "fps_peak": {"label": "最高帧率", "unit": "fps", "kind": "number", "conditions": ["game", "resolution"]},
    "fps_1pct_low": {"label": "1% Low 帧率", "unit": "fps", "kind": "number", "conditions": ["game", "resolution"]},
    "power": {"label": "整机功耗", "unit": "W", "kind": "number", "conditions": ["game", "resolution", "duration_min"]},
    "temp": {"label": "机身温度", "unit": "C", "kind": "number", "conditions": ["game", "duration_min", "measure_point"]},
    "main_camera": {"label": "主摄", "unit": "", "kind": "text", "conditions": []},
    "ultrawide": {"label": "超广角", "unit": "", "kind": "text", "conditions": []},
    "telephoto": {"label": "长焦", "unit": "", "kind": "text", "conditions": []},
    "speaker": {"label": "扬声器", "unit": "", "kind": "text", "conditions": []},
    "usb": {"label": "USB", "unit": "", "kind": "text", "conditions": []},
    "ip_rating": {"label": "防护等级", "unit": "", "kind": "text", "conditions": []},
    "fingerprint": {"label": "指纹", "unit": "", "kind": "text", "conditions": []},
    "weight_note": {"label": "手感备注", "unit": "", "kind": "text", "conditions": []},
}

ALIASES = {
    "价格": "price", "售价": "price", "起售价": "price",
    "芯片": "soc", "处理器": "soc", "soc": "soc",
    "屏幕尺寸": "screen_size", "尺寸": "screen_size",
    "刷新率": "screen_refresh", "高刷": "screen_refresh",
    "亮度": "screen_brightness", "激发亮度": "screen_brightness", "峰值亮度": "screen_brightness",
    "电池": "battery", "电池容量": "battery",
    "充电功率": "charge_power", "快充": "charge_power",
    "充满时间": "charge_time", "充电时间": "charge_time",
    "重量": "weight", "厚度": "thickness",
    "续航": "battery_life", "亮屏续航": "battery_life", "亮屏时间": "battery_life",
    "帧率": "fps", "平均帧率": "fps",
    "功耗": "power", "整机功耗": "power",
    "温度": "temp", "机身温度": "temp", "发热": "temp",
    "主摄": "main_camera", "主摄传感器": "main_camera",
    "超广角": "ultrawide", "长焦": "telephoto",
    "扬声器": "speaker", "外放": "speaker",
    "usb": "usb", "接口": "usb",
    "防水": "ip_rating", "防护": "ip_rating", "ip68": "ip_rating",
    "指纹": "fingerprint", "马达": "weight_note",
}

PRIORITY: List[str] = [
    "price", "soc", "screen_size", "screen_refresh", "screen_brightness", "screen_peak_brightness",
    "screen_manual_brightness", "screen_brightness_ingame",
    "battery", "charge_power", "charge_wireless_power", "charge_time", "battery_life",
    "fps", "fps_1pct_low", "fps_peak", "power", "temp", "weight", "thickness",
    "main_camera", "ultrawide", "telephoto", "speaker", "usb",
    "ip_rating", "fingerprint",
]

CONDITION_KEYS = {
    "类型": "type", "充电方式": "method", "亮度模式": "mode", "屏幕模式": "mode",
    "游戏": "game", "分辨率": "resolution", "画质": "quality",
    "时长": "duration_min", "位置": "measure_point", "机身": "measure_point",
    "存储版本": "storage", "存储": "storage", "指标": "stat",
    "网络": "network", "亮度": "brightness_nit",
}


def canon_condition_key(key: str) -> str:
    return CONDITION_KEYS.get(str(key or "").strip(), str(key or "").strip())


def reclassify(metric: str, condition: dict) -> str:
    """Split look-alike metrics apart before comparison."""
    text = " ".join(str(v) for v in condition.values())
    if metric == "screen_brightness":
        if "手动" in text:
            return "screen_manual_brightness"
        if condition.get("game"):
            return "screen_brightness_ingame"
    if metric == "charge_power" and "无线" in text:
        return "charge_wireless_power"
    if metric == "fps":
        stat = str(condition.get("stat", "")).lower()
        if "1%" in stat or "low" in stat:
            return "fps_1pct_low"
        if "最高" in stat or "peak" in stat:
            return "fps_peak"
    return metric


def canonical(metric: str) -> Optional[str]:
    text = str(metric or "").strip()
    if text in METRICS:
        return text
    lowered = text.lower()
    if lowered in METRICS:
        return lowered
    for alias, key in ALIASES.items():
        if alias in text or text in alias:
            return key
    return None


def label(metric: str) -> str:
    return str((METRICS.get(metric) or {}).get("label", metric))


def unit(metric: str) -> str:
    return str((METRICS.get(metric) or {}).get("unit", ""))


def kind(metric: str) -> str:
    return str((METRICS.get(metric) or {}).get("kind", "text"))


def conditions(metric: str) -> List[str]:
    return list((METRICS.get(metric) or {}).get("conditions", []))
