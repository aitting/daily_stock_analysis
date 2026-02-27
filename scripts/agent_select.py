#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Agent Stock Selection Script
=============================
Runs the `short_term_selection` strategy via the Agent framework to pick
up to 10 A-share candidates for the day.

Outputs a comma-separated list of stock codes to stdout (and optionally to
a file) so the CI workflow can inject them as STOCK_LIST for the main analysis.

Usage:
    python scripts/agent_select.py                          # print to stdout
    python scripts/agent_select.py --output data/selected_stocks.txt
    python scripts/agent_select.py --strategy short_term_selection
    python scripts/agent_select.py --max-stocks 5
"""

import argparse
import logging
import os
import re
import sys
from pathlib import Path

# --- setup_env MUST come before any src.* import ---
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import setup_env
setup_env()

from src.config import get_config
from src.logging_config import setup_logging
from src.agent.factory import build_agent_executor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# A-share code patterns
# ---------------------------------------------------------------------------

# Matches bare 6-digit codes like 600519, 000001, 300750, with optional
# exchange prefix/suffix (SH/SZ/.SH/.SZ) that we strip afterwards.
_ASTOCK_FULL  = re.compile(r'\b(?:SH|SZ)?(?P<code>[036]\d{5}|[12]\d{5})(?:\.(?:SH|SZ))?\b', re.IGNORECASE)
# HK: HK followed by 5 digits
_HKSTOCK_FULL = re.compile(r'\bHK\d{5}\b', re.IGNORECASE)
# US: 1-5 uppercase letters only (avoid matching CJK content)
_USSTOCK_FULL = re.compile(r'\b[A-Z]{1,5}\b')

# A-shares whose first digit indicates a valid exchange listing
_VALID_FIRST = set("036")  # 6=SH main board, 3=GEM/STAR, 0=SZ main board
# Extended: 1=SH B-share, 2=SZ B-share — included but less common


def _extract_stock_codes(text: str, market: str = "cn") -> list[str]:
    """
    Parse selected stock codes from the agent's free-text output.

    Strategy:
      1. Look for explicit code patterns (6-digit, HK, US ticker).
      2. De-duplicate while preserving first-appearance order.
      3. For A-shares, only keep codes starting with 0/3/6 (main boards + GEM/STAR).
    """
    seen: dict[str, None] = {}  # ordered set

    for m in _ASTOCK_FULL.finditer(text):
        code = m.group("code")
        if code[0] in _VALID_FIRST or code[0] in "12":
            seen.setdefault(code)

    if market in ("hk", "mixed"):
        for m in _HKSTOCK_FULL.finditer(text):
            seen.setdefault(m.group(0).upper())

    if market in ("us", "mixed"):
        # Only take US tickers that appear alongside a price or % pattern
        for m in _USSTOCK_FULL.finditer(text):
            ticker = m.group(0)
            # Rough heuristic: ticker is preceded or followed by currency/pct
            span_start = max(0, m.start() - 20)
            span_end   = min(len(text), m.end() + 20)
            context    = text[span_start:span_end]
            if re.search(r'[$%￥¥\d]', context):
                seen.setdefault(ticker)

    return list(seen.keys())


def _build_selection_prompt(max_stocks: int) -> str:
    return (
        "请立即执行【A股短线选股策略】，完成三步选股流程：\n"
        "1. 先用 get_market_indices 判断当前大盘环境；\n"
        "2. 再用 get_sector_rankings 定位今日最强主线板块；\n"
        "3. 最后在主线板块中，结合技术面和资金面，精选不超过 "
        f"{max_stocks} 只短线个股。\n\n"
        "输出格式要求：\n"
        "- 首先给出市场状态判断（好行情 / 轮动市 / 冰点期）；\n"
        "- 冰点期则直接输出「市场风险提示」，不输出选股结果；\n"
        "- 其他状态：对每只入选股票输出：股票代码（6位）、股票名称、"
        "所属主线板块、核心入选理由（一句话）、建议入场区间和止损位。\n"
        "- 最后单独一行输出：「入选代码：XXXXXX,YYYYYY,...」"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent-driven A-share stock selection")
    parser.add_argument("--strategy", default="short_term_selection",
                        help="Strategy id to activate (default: short_term_selection)")
    parser.add_argument("--max-stocks", type=int, default=10,
                        help="Maximum number of stocks to select (default: 10)")
    parser.add_argument("--output", type=str, default=None,
                        help="Write comma-separated codes to this file path")
    parser.add_argument("--report", type=str, default=None,
                        help="Write full agent report text to this file path")
    parser.add_argument("--market", default="cn",
                        choices=["cn", "hk", "us", "mixed"],
                        help="Market scope for code extraction (default: cn)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(debug=args.debug)

    config = get_config()

    logger.info("[agent_select] Building executor with strategy: %s", args.strategy)
    try:
        executor = build_agent_executor(config, skills=[args.strategy])
    except Exception as exc:
        logger.error("[agent_select] Failed to build executor: %s", exc)
        return 1

    prompt = _build_selection_prompt(args.max_stocks)
    logger.info("[agent_select] Sending selection prompt to agent...")

    import uuid
    result = executor.run(task=prompt, context={"query_id": str(uuid.uuid4())})

    if not result.success:
        logger.error("[agent_select] Agent execution failed: %s", result.error)
        # On failure print empty line so the workflow can detect no-op gracefully
        print("")
        return 1

    report_text = result.content
    logger.info("[agent_select] Agent returned %d chars", len(report_text))

    # --- save full report if requested ---
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report_text, encoding="utf-8")
        logger.info("[agent_select] Full report saved to %s", args.report)

    # --- extract codes ---
    codes = _extract_stock_codes(report_text, market=args.market)

    # Honor max_stocks limit
    codes = codes[: args.max_stocks]

    if not codes:
        logger.warning("[agent_select] No stock codes found in agent output.")
        logger.warning("[agent_select] --- agent output (first 500 chars) ---\n%s", report_text[:500])
        print("")
        return 0

    csv_codes = ",".join(codes)
    logger.info("[agent_select] Selected %d stocks: %s", len(codes), csv_codes)

    # --- write to file if requested ---
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(csv_codes, encoding="utf-8")
        logger.info("[agent_select] Codes saved to %s", args.output)

    # Always print to stdout (captured by the workflow)
    print(csv_codes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
