"""Universe of tickers analyzed for the semiconductor sector report.

The lists are intentionally small and curated, not exhaustive — every ticker
here should be load-bearing for the analysis layers.
"""
from dataclasses import dataclass


# Sector ETFs — proxies for the broad semi industry
SEMI_ETFS = ["SMH", "SOXX", "SOXL"]   # SOXL = 3x leveraged, user holds

# Broad market benchmarks (for relative strength).
# QQQE (equal-weight Nasdaq-100) / QQQ ratio = breadth proxy that doesn't
# depend on our survivor-biased hand-picked universe.
BENCHMARKS = ["SPY", "QQQ", "QQQE"]

# AI Infrastructure (power + datacenter + components) — second analysis theme
AI_INFRA_LEADERS = [
    "GEV",   # GE Vernova — grid / electrification
    "VRT",   # Vertiv — datacenter power + cooling
    "ETN",   # Eaton — electrical infrastructure
    "PWR",   # Quanta Services — electrical contractors
    "EMR",   # Emerson Electric
    "EQIX",  # Equinix — datacenter REIT
    "DLR",   # Digital Realty — datacenter REIT
    "ANET",  # Arista — datacenter networking
]

# Curated leaders. Mix of:
#   - GPU/AI compute: NVDA, AMD
#   - Custom silicon / networking: AVGO
#   - Foundry: TSM
#   - Memory: MU
#   - Equipment: AMAT, LRCX, KLA, ASML
#   - Laggard: INTC (kept as a sentiment check)
SEMI_LEADERS = [
    "NVDA", "AMD", "AVGO", "TSM", "MU",
    "AMAT", "LRCX", "KLAC", "ASML", "INTC",   # KLA Corp 代码是 KLAC (不是 KLA)
]

# Macro context. yfinance symbols:
#   ^TNX = CBOE 10-year US Treasury yield index (yfinance returns as actual %)
#   DX-Y.NYB = US Dollar Index
#   ^VIX = volatility index; ^VIX3M = 3-month VIX (term structure:
#   spot/3M > 1 = backwardation = stress, a far more robust risk-off signal
#   than any spot-VIX level threshold)
MACRO = ["^TNX", "DX-Y.NYB", "^VIX", "^VIX3M"]


# Cutting-edge sector additions (核电, 光通信, 量子, 机器人/自动化)
NUCLEAR_LEADERS = [
    "CCJ",   # Cameco — uranium miner (largest pure play)
    "CEG",   # Constellation Energy — operates US nuclear fleet
    "VST",   # Vistra — nuclear + power, AI datacenter PPA wins
    "OKLO",  # Oklo — small modular reactor (SMR) startup
    "SMR",   # NuScale Power — pioneering SMR
    "BWXT",  # BWX Technologies — naval / SMR components
    "LEU",   # Centrus Energy — uranium enrichment
]

OPTICAL_LEADERS = [
    "LITE",  # Lumentum — optical components, NVDA supplier
    "COHR",  # Coherent — lasers, photonics (II-VI merged)
    "FN",    # Fabrinet — optical packaging for hyperscalers
    "CIEN",  # Ciena — optical networking
    "AAOI",  # Applied Optoelectronics — transceivers
    "VIAV",  # Viavi Solutions — optical test equipment
]

QUANTUM_LEADERS = [
    "IONQ",  # IonQ — trapped-ion quantum computing
    "RGTI",  # Rigetti — superconducting qubits
    "QBTS",  # D-Wave — quantum annealing
]

ROBOTICS_LEADERS = [
    "ISRG",  # Intuitive Surgical — surgical robots
    "TER",   # Teradyne — test + collaborative robots
    "ABBNY", # ABB Ltd ADR (OTC) — NYSE 的 ABB 已退市, Yahoo 无数据
    "ROK",   # Rockwell Automation
]


# 10x candidates — small/mid cap with explosive narrative potential.
# These are EXPLICITLY low quality by QMJ standards (often unprofitable) and
# extremely volatile. Sized appropriately, they're asymmetric bets where one
# winner can carry the basket. Curated across 7 high-narrative themes.
MOONSHOT_LEADERS = [
    # 量子计算 (全部, 包括已在 QUANTUM_LEADERS 的, 为方便横向比较)
    "IONQ",   # IonQ — trapped-ion (用户已持仓)
    "RGTI",   # Rigetti — superconducting
    "QBTS",   # D-Wave — annealing
    "QUBT",   # Quantum Computing Inc — photonic quantum
    "ARQQ",   # Arqit Quantum — UK encryption + quantum

    # 太空经济
    "RKLB",   # Rocket Lab — Neutron rocket, NVDA partner
    "ASTS",   # AST SpaceMobile — direct-to-cell satellite
    "ACHR",   # Archer Aviation — eVTOL air taxi
    "JOBY",   # Joby Aviation — eVTOL competitor

    # 小型核电 / 先进核能
    "NNE",    # Nano Nuclear Energy
    "LTBR",   # Lightbridge — advanced nuclear fuel
    "ASPI",   # ASP Isotopes — medical isotope production

    # 国防 AI / 无人系统
    "BBAI",   # BigBear.ai — defense AI analytics
    "AVAV",   # AeroVironment — military drones
    "KTOS",   # Kratos Defense — drones, missiles

    # AI 软件新兴
    "SOUN",   # SoundHound AI — voice AI
    "PATH",   # UiPath — RPA / agentic automation
    "AI",     # C3.ai — enterprise AI

    # 基因编辑 / 合成生物学
    "CRSP",   # CRISPR Therapeutics
    "NTLA",   # Intellia Therapeutics
    "BEAM",   # Beam Therapeutics

    # 储能 / 先进材料
    "ENVX",   # Enovix — silicon anode batteries
    "QS",     # QuantumScape — solid-state batteries

    # 加密代理 (BTC beta plays)
    "MARA",   # Marathon Digital
    "CLSK",   # CleanSpark
]


# Broad "hot tech" universe for the daily Discord scanner.
# Curated to span: Mag 7, semis, AI infra, AI-software, frontier tech, ETFs.
# dict.fromkeys dedup: IONQ/RGTI/QBTS appear in both QUANTUM_LEADERS and
# MOONSHOT_LEADERS — without dedup they were double-counted in breadth,
# movers and action levels.
HOT_TECH = list(dict.fromkeys(
    # Mag 7 + adjacent mega-cap
    ["AAPL", "MSFT", "GOOG", "AMZN", "NVDA", "META", "TSLA"]
    # Semi leaders
    + ["AMD", "AVGO", "TSM", "ARM", "MU", "INTC", "ASML", "AMAT", "LRCX"]
    # AI infrastructure / data center
    + ["GEV", "VRT", "ETN", "PWR", "EQIX", "ANET"]
    # AI-software / high-growth
    + ["PLTR", "SMCI", "CRWD", "NET"]
    # Frontier tech
    + NUCLEAR_LEADERS + OPTICAL_LEADERS + QUANTUM_LEADERS + ROBOTICS_LEADERS
    # 10x candidates (small caps, explicitly high vol)
    + MOONSHOT_LEADERS
    # Sector / levered ETFs (context references)
    + ["SMH", "SOXX", "QQQ", "XLK", "SOXL", "TQQQ", "TECL"]
))


def categorize_hot_tech(ticker: str) -> str:
    """Group a hot-tech ticker for display purposes."""
    if ticker in {"AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA"}:
        return "Mag 7"
    if ticker in {"NVDA", "AMD", "AVGO", "TSM", "ARM", "MU", "INTC",
                  "ASML", "AMAT", "LRCX"}:
        return "Semi"
    if ticker in {"GEV", "VRT", "ETN", "PWR", "EQIX", "ANET"}:
        return "AI Infra"
    if ticker in {"PLTR", "SMCI", "CRWD", "NET"}:
        return "AI SW / 高成长"
    if ticker in set(NUCLEAR_LEADERS):
        return "核电/铀"
    if ticker in set(OPTICAL_LEADERS):
        return "光通信"
    if ticker in set(QUANTUM_LEADERS):
        return "量子计算"
    if ticker in set(ROBOTICS_LEADERS):
        return "机器人/自动化"
    if ticker in set(MOONSHOT_LEADERS):
        return "10x候选"
    if ticker in {"SOXL", "TQQQ", "TECL"}:
        return "Leveraged ETF"
    if ticker in {"SMH", "SOXX", "QQQ", "XLK"}:
        return "Sector ETF"
    return "Other"


CAD_LISTED_SUFFIXES = (".TO", ".NE", ".V", ".CN")


def is_cad_listed(ticker: str) -> bool:
    """CAD-denominated listings: TSX (.TO), Cboe Canada / NEO (.NE — where
    Wealthsimple's CDRs live, e.g. BRK.NE, MSFT.NE), TSXV (.V), CSE (.CN).
    Everything else is treated as USD for FX conversion."""
    return ticker.upper().endswith(CAD_LISTED_SUFFIXES)


@dataclass
class Holding:
    ticker: str
    shares: float
    cost_basis: float       # total CAD invested
    sector: str
    note: str = ""

    @property
    def market_value(self) -> float:
        # filled in by analyzer after price fetch
        return getattr(self, "_market_value", 0.0)

    def set_market_value(self, v: float) -> None:
        self._market_value = v

    @property
    def pnl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def pnl_pct(self) -> float:
        if self.cost_basis <= 0:
            return 0.0
        return (self.market_value - self.cost_basis) / self.cost_basis


def all_symbols_for_run(holdings: list[Holding]) -> list[str]:
    """Every symbol we need to fetch for one analysis run."""
    symbols = set()
    symbols.update(SEMI_ETFS)
    symbols.update(BENCHMARKS)
    symbols.update(SEMI_LEADERS)
    symbols.update(AI_INFRA_LEADERS)
    symbols.update(HOT_TECH)
    symbols.update(MACRO)
    for h in holdings:
        symbols.add(h.ticker)
    return sorted(symbols)
