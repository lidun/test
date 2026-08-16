"""LLM 策略生成器：负责生成、变异、杂交策略

支持多家国内 LLM（通过 OpenAI 兼容协议）。当 LLM 不可用时，
自动降级为内置经典策略模板，保证系统可运行。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.config import config
from src.llm.base import LLMClient, MockLLMClient
from src.llm.factory import create_client


class LLMStrategyGenerator:
    PROMPT_DIR = Path(__file__).parent / "prompts"

    def __init__(self, knowledge_retriever=None):
        self.knowledge = knowledge_retriever
        self.client: LLMClient = create_client()
        self.prompts = self._load_prompts()
        logger.info(f"LLM 策略生成器初始化完成，provider={self.client.name}, model={self.client.model}")

    @property
    def available(self) -> bool:
        return not isinstance(self.client, MockLLMClient)

    def _safe_format(self, prompt: str, **kwargs) -> str:
        """按占位符名替换，避免模板内 JSON 花括号被 format 误解析"""
        for key, val in kwargs.items():
            prompt = prompt.replace("{" + key + "}", str(val))
        return prompt

    def _load_prompts(self) -> dict:
        prompts = {}
        for name in ("generate", "mutate", "crossover", "analyze"):
            path = self.PROMPT_DIR / f"{name}.txt"
            if path.exists():
                prompts[name] = path.read_text(encoding="utf-8")
        return prompts

    def generate_new_strategies(
        self,
        market_context: Optional[dict] = None,
        n_strategies: int = 3,
        strategy_type: Optional[str] = None,
    ) -> List[dict]:
        market_context = market_context or {}
        knowledge = ""
        if self.knowledge:
            knowledge = self.knowledge.retrieve_for_strategy_generation(
                market_context, strategy_type
            )

        user_prompt = self._build_generation_prompt(market_context, knowledge, n_strategies)
        try:
            response = self._call_llm(
                self.prompts.get("generate", ""),
                user_prompt,
                temperature=config.llm.temperature_generate,
                json_mode=True,
            )
            result = self._parse_json(response)
            strategies = result.get("strategies", [])
            if strategies:
                return strategies[:n_strategies]
            logger.warning("LLM 返回空策略列表，使用内置模板")
        except Exception as e:
            logger.warning(f"LLM 生成策略失败: {e}，使用内置模板")
        return self._fallback_strategies(n_strategies, strategy_type)

    def mutate_strategy(self, strategy: dict, performance: dict) -> Optional[dict]:
        prompt = self.prompts.get("mutate", "")
        if not prompt:
            return None
        try:
            response = self._call_llm(
                self._safe_format(
                    prompt,
                    original_strategy=json.dumps(strategy, indent=2, ensure_ascii=False),
                    performance_summary=json.dumps(performance, indent=2, ensure_ascii=False),
                ),
                "请生成改进版本。",
                temperature=config.llm.temperature_mutate,
                json_mode=True,
            )
            return self._parse_json(response)
        except Exception as e:
            logger.warning(f"LLM 变异策略失败: {e}")
            return self._fallback_mutation(strategy)

    def crossover_strategies(
        self, strategy_a: dict, strategy_b: dict, perf_a: dict, perf_b: dict
    ) -> Optional[dict]:
        prompt = self.prompts.get("crossover", "")
        if not prompt:
            return None
        try:
            response = self._call_llm(
                self._safe_format(
                    prompt,
                    strategy_a=json.dumps(strategy_a, indent=2, ensure_ascii=False),
                    strategy_b=json.dumps(strategy_b, indent=2, ensure_ascii=False),
                    performance_a=json.dumps(perf_a, indent=2, ensure_ascii=False),
                    performance_b=json.dumps(perf_b, indent=2, ensure_ascii=False),
                ),
                "请生成融合策略。",
                temperature=config.llm.temperature_mutate,
                json_mode=True,
            )
            return self._parse_json(response)
        except Exception as e:
            logger.warning(f"LLM 杂交策略失败: {e}")
            return self._fallback_crossover(strategy_a, strategy_b)

    def analyze_strategy(self, strategy: dict) -> dict:
        prompt = self.prompts.get("analyze", "")
        if not prompt:
            return {}
        try:
            response = self._call_llm(
                self._safe_format(
                    prompt, strategy=json.dumps(strategy, indent=2, ensure_ascii=False)
                ),
                "请分析策略风险。",
                temperature=config.llm.temperature_analyze,
                json_mode=True,
            )
            return self._parse_json(response)
        except Exception as e:
            logger.warning(f"LLM 分析策略失败: {e}")
            return {"risk_level": "medium", "main_risks": [], "suggestions": []}

    def _build_generation_prompt(self, market_context: dict, knowledge: str, n: int) -> str:
        return f"""
## 当前市场环境
- 市场趋势：{market_context.get('trend', '震荡')}
- 波动率水平：{market_context.get('volatility', '中等')}
- 热点行业：{', '.join(market_context.get('hot_sectors', []))}
- 宏观环境：{market_context.get('macro', '中性')}

## 相关知识库
{knowledge}

## 任务
请生成{n}个适合当前市场环境的选股策略。
"""

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        json_mode: bool = False,
        max_retries: int = 2,
    ) -> str:
        last_err = None
        for attempt in range(max_retries + 1):
            try:
                return self.client.chat(
                    system_prompt, user_prompt, temperature=temperature, json_mode=json_mode
                )
            except Exception as e:
                last_err = e
                logger.warning(f"LLM 调用失败 (attempt {attempt+1}/{max_retries+1}): {e}")
                time.sleep(2 ** attempt)
        raise RuntimeError(f"LLM 调用失败: {last_err}")

    def _parse_json(self, text: str) -> dict:
        if not text:
            return {}
        text = text.strip()
        # 兼容 markdown 代码块包裹
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 扫描第一个 { 并尝试 raw_decode，兼容带前缀文本
            idx = 0
            while True:
                start = text.find("{", idx)
                if start < 0:
                    break
                try:
                    obj, _ = json.JSONDecoder().raw_decode(text[start:])
                    return obj
                except json.JSONDecodeError:
                    idx = start + 1
            raise ValueError(f"无法解析 LLM 返回的 JSON: {text[:200]}")

    # ============ 内置降级策略模板 ============
    def _fallback_strategies(self, n: int, strategy_type: Optional[str]) -> List[dict]:
        templates = self._builtin_templates()
        if strategy_type:
            matched = [t for t in templates if t["type"] == strategy_type]
            if matched:
                templates = matched + templates
        return templates[:n]

    def _fallback_mutation(self, strategy: dict) -> dict:
        mutant = dict(strategy)
        mutant["name"] = f"{strategy.get('name', '策略')}-变异版"
        mutant["generation_method"] = "mutate"
        # 微调止损止盈，增强稳健性
        mutant["stop_loss"] = min(strategy.get("stop_loss", -0.08) - 0.02, -0.03)
        mutant["stop_profit"] = strategy.get("stop_profit", 0.30) * 0.9
        mutant["risk_warning"] = (strategy.get("risk_warning", "") or "") + "；变异版已收紧止损"
        return mutant

    def _fallback_crossover(self, a: dict, b: dict) -> dict:
        a_conds = a.get("conditions", [])[:2]
        b_conds = b.get("conditions", [])[:2]
        hybrid = {
            "name": f"{a.get('name', 'A')}×{b.get('name', 'B')} 杂交",
            "type": "hybrid",
            "core_hypothesis": f"融合 {a.get('name', '策略A')} 与 {b.get('name', '策略B')} 的选股逻辑",
            "logic_explanation": "组合两种不同因子的互补性，降低单一因子失效风险",
            "expected_market_regime": "震荡与趋势兼备",
            "conditions": a_conds + b_conds,
            "ranking_factors": (a.get("ranking_factors", [])[:2] + b.get("ranking_factors", [])[:2])[:3],
            "rebalance_freq": "monthly",
            "stop_loss": min(a.get("stop_loss", -0.08), b.get("stop_loss", -0.08)),
            "stop_profit": max(a.get("stop_profit", 0.30), b.get("stop_profit", 0.30)),
            "max_position": max(a.get("max_position", 10), b.get("max_position", 10)),
            "risk_warning": "杂交策略因子相关性需持续监控",
        }
        hybrid["generation_method"] = "crossover"
        return hybrid

    def _builtin_templates(self) -> List[dict]:
        return [
            {
                "name": "低估值价值精选",
                "type": "value",
                "core_hypothesis": "低估值股票在长期存在均值回归，价值因子在A股长期有效",
                "logic_explanation": "选择市盈率与市净率双低的股票，同时要求一定盈利能力，避免价值陷阱",
                "expected_market_regime": "震荡市与熊市",
                "conditions": [
                    {"factor": "pe_ttm", "operator": "BETWEEN", "value": [0, 20], "rationale": "估值处于合理偏低区间"},
                    {"factor": "pb", "operator": "<", "value": 3, "rationale": "市净率偏低"},
                    {"factor": "volatility_20d", "operator": "<", "value": 0.5, "rationale": "排除高波动风险股"},
                ],
                "ranking_factors": [
                    {"factor": "pe_ttm", "weight": 0.6, "direction": "ascending"},
                    {"factor": "pb", "weight": 0.4, "direction": "ascending"},
                ],
                "rebalance_freq": "monthly",
                "stop_loss": -0.10,
                "stop_profit": 0.30,
                "max_position": 20,
                "risk_warning": "价值陷阱风险：低估值可能因基本面持续恶化而继续下跌",
            },
            {
                "name": "中期动量趋势",
                "type": "momentum",
                "core_hypothesis": "A股存在中期动量效应，强势股在3-6个月内倾向延续趋势",
                "logic_explanation": "选择20-60日中期动量为正且价格站上均线的股票，顺势而为",
                "expected_market_regime": "牛市与上升趋势",
                "conditions": [
                    {"factor": "ret_20d", "operator": ">", "value": 0.05, "rationale": "中期动量为正"},
                    {"factor": "ret_60d", "operator": ">", "value": 0.10, "rationale": "长期趋势向上"},
                    {"factor": "close_ma_60", "operator": ">", "value": 1.0, "rationale": "价格在60日均线上方"},
                ],
                "ranking_factors": [
                    {"factor": "ret_20d", "weight": 0.6, "direction": "descending"},
                    {"factor": "ret_60d", "weight": 0.4, "direction": "descending"},
                ],
                "rebalance_freq": "weekly",
                "stop_loss": -0.08,
                "stop_profit": 0.35,
                "max_position": 10,
                "risk_warning": "熊市中动量策略回撤大，需配合大盘趋势过滤",
            },
            {
                "name": "高股息防守",
                "type": "value",
                "core_hypothesis": "高股息股票现金流稳定，在弱市中具有防御属性",
                "logic_explanation": "选择股息率高且波动率低的股票，获取稳定分红与防御收益",
                "expected_market_regime": "熊市与震荡市",
                "conditions": [
                    {"factor": "dividend_yield", "operator": ">", "value": 0.03, "rationale": "股息率大于3%"},
                    {"factor": "volatility_20d", "operator": "<", "value": 0.4, "rationale": "波动率较低，防守属性强"},
                ],
                "ranking_factors": [
                    {"factor": "dividend_yield", "weight": 0.7, "direction": "descending"},
                    {"factor": "volatility_20d", "weight": 0.3, "direction": "ascending"},
                ],
                "rebalance_freq": "monthly",
                "stop_loss": -0.06,
                "stop_profit": 0.20,
                "max_position": 20,
                "risk_warning": "股息陷阱：需验证分红持续性，防止财务造假",
            },
            {
                "name": "超跌反弹捕捉",
                "type": "event",
                "core_hypothesis": "A股存在显著短期反转效应，超跌股票存在均值回归机会",
                "logic_explanation": "捕捉短期大幅下跌但未破长期趋势的股票，博取超跌反弹",
                "expected_market_regime": "震荡市",
                "conditions": [
                    {"factor": "ret_5d", "operator": "<", "value": -0.08, "rationale": "5日超跌"},
                    {"factor": "ret_60d", "operator": ">", "value": -0.15, "rationale": "中期趋势未严重破坏"},
                    {"factor": "volume_ratio", "operator": ">", "value": 1.2, "rationale": "下跌伴随放量，反转概率高"},
                ],
                "ranking_factors": [
                    {"factor": "ret_5d", "weight": 0.6, "direction": "ascending"},
                    {"factor": "volume_ratio", "weight": 0.4, "direction": "descending"},
                ],
                "rebalance_freq": "weekly",
                "stop_loss": -0.07,
                "stop_profit": 0.15,
                "max_position": 10,
                "risk_warning": "超跌可能继续下跌，需严格控制止损",
            },
            {
                "name": "技术形态突破",
                "type": "technical",
                "core_hypothesis": "放量突破关键技术位往往预示趋势延续",
                "logic_explanation": "MACD金叉伴随量比放大与价格突破均线，捕捉技术性突破行情",
                "expected_market_regime": "牛市中段",
                "conditions": [
                    {"factor": "macd_dif", "operator": ">", "value": 0, "rationale": "MACD多头"},
                    {"factor": "volume_ratio", "operator": ">", "value": 1.5, "rationale": "明显放量"},
                    {"factor": "close_ma_20", "operator": ">", "value": 1.0, "rationale": "站上20日均线"},
                ],
                "ranking_factors": [
                    {"factor": "volume_ratio", "weight": 0.5, "direction": "descending"},
                    {"factor": "ret_20d", "weight": 0.5, "direction": "descending"},
                ],
                "rebalance_freq": "weekly",
                "stop_loss": -0.08,
                "stop_profit": 0.25,
                "max_position": 8,
                "risk_warning": "假突破风险高，需验证成交量配合",
            },
        ]
