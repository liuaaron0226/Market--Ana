"""【已退役 2026-11-08】本人定案「自創部分不保留」；個股分析改由 analyze_v2.py 輸出（契約 .scratch/chart-screening-workbench/stock-analysis-contract-v2.md）。本檔僅為既有呼叫端相容而保留，不用於新產品輸出。

本站自有分析方案（非交易策略，也非原站公式重現）。

計分沿用本站 reference presets；價位來源／窗口組合為助手自有分析設定，
未經收益驗證。只切換候選來源與窗口，不改 24 招條件、五維基礎算法或分群規則。
公開 metadata 採白名單；權重與窗口留在後端，指紋涵蓋有效設定及版本。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from types import MappingProxyType

DEFAULT = '2TON-FIT-V1'


@dataclass(frozen=True)
class PriceProfile:
    ma_periods: tuple[int, ...] = (5, 10, 20, 60, 120, 240)
    vp_window: int = 60
    swing_gap_window: int = 120
    breakout_scan: int = 60
    platform_window: int = 40
    heavy_window: int = 60
    heavy_count: int = 9
    include_long_refs: bool = True
    include_inst_cost: bool = True


@dataclass(frozen=True)
class ScoreProfile:
    T: float = .40
    C: float = .10
    V: float = .25
    I: float = .15
    F: float = .10
    res: int = 6
    oh_rsi: int = 6
    oh_margin: int = 6
    oh_hot: int = 4
    risk: int = 6


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    description: str
    version: str
    score: ScoreProfile
    price: PriceProfile

    def public_metadata(self) -> dict:
        return {key: getattr(self, key) for key in ('id', 'name', 'description', 'version')}

    @property
    def formula(self) -> str:
        return f'{self.id}+price:site-profile-v1'

    @property
    def params_hash(self) -> str:
        effective = {'score': asdict(self.score), 'price': asdict(self.price), 'version': self.version}
        return hashlib.sha256(json.dumps(effective, sort_keys=True).encode()).hexdigest()[:8]


_PRESETS = MappingProxyType({
    DEFAULT: Preset(
        DEFAULT, '綜合觀察', '沿用既有綜合計分與價位觀察；本站自有分析，非交易指令。',
        f'MA-TWON-ANALYSIS-PV2:{DEFAULT}', ScoreProfile(), PriceProfile()),
    'MOMENTUM-V1': Preset(
        'MOMENTUM-V1', '短線量價', '偏重短線趨勢與量價，採較短價位窗口；本站自有分析，非收益保證。',
        'MA-TWON-ANALYSIS-PV2:MOMENTUM-V1',
        ScoreProfile(T=.40, C=.15, V=.30, I=.10, F=.05, res=8, oh_rsi=5, oh_margin=6, oh_hot=3, risk=6),
        PriceProfile(ma_periods=(5, 10, 20), vp_window=20, swing_gap_window=40, breakout_scan=20,
                     platform_window=20, heavy_window=20, heavy_count=3,
                     include_long_refs=False, include_inst_cost=False)),
    'SWING-SAFE-V1': Preset(
        'SWING-SAFE-V1', '波段結構', '偏重法人與基本面，採較長價位窗口；本站自有分析，名稱不代表保本。',
        'MA-TWON-ANALYSIS-PV2:SWING-SAFE-V1',
        ScoreProfile(T=.30, C=.15, V=.10, I=.25, F=.20, res=5, oh_rsi=7, oh_margin=7, oh_hot=5, risk=7),
        PriceProfile(ma_periods=(20, 60, 120, 240), vp_window=120, swing_gap_window=240, breakout_scan=120,
                     platform_window=80, heavy_window=120, heavy_count=18,
                     include_long_refs=True, include_inst_cost=True)),
})


def get_preset(preset_id: str = DEFAULT) -> Preset:
    if not isinstance(preset_id, str) or preset_id not in _PRESETS:
        raise ValueError('未知分析方案')
    return _PRESETS[preset_id]


def available_presets() -> list[dict]:
    return [preset.public_metadata() for preset in _PRESETS.values()]
