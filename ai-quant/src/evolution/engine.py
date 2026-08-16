"""进化引擎：淘汰→精英识别→变异→杂交→新生→验证→上线"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy import text

from src.core.config import config
from src.core.database import get_db_session


class EvolutionEngine:
    def __init__(self, generator, compiler, validator, arena, knowledge_retriever):
        self.generator = generator
        self.compiler = compiler
        self.validator = validator
        self.arena = arena
        self.knowledge = knowledge_retriever
        self.elimination_rate = config.evolution.elimination_rate
        self.elite_count = config.evolution.elite_count
        self.mutants_per_elite = config.evolution.mutants_per_elite
        self.new_per_cycle = config.evolution.new_strategies_per_cycle
        self.enable_crossover = config.evolution.enable_crossover
        self.evolution_cycle = 0

    async def evolve(self, market_context: Optional[dict] = None) -> dict:
        self.evolution_cycle += 1
        cycle = self.evolution_cycle
        logger.info(f"进化周期 #{cycle} 开始")

        # 确保竞技场非空（从数据库恢复或注入种子策略）
        self._ensure_arena_populated()

        # Phase 1: 评估
        leaderboard = self.arena.get_leaderboard()
        if len(leaderboard) == 0:
            logger.warning("竞技场为空，跳过进化")
            return {"cycle": cycle, "error": "arena_empty"}

        # Phase 2: 淘汰末位
        n_eliminate = max(1, int(len(leaderboard) * self.elimination_rate))
        n_eliminate = min(n_eliminate, len(leaderboard) - 1)
        worst = leaderboard.tail(n_eliminate)
        eliminated_ids = []
        for _, row in worst.iterrows():
            sid = row.get("strategy_id")
            if sid and sid in self.arena.strategies:
                self._mark_eliminated(sid)
                self.arena.remove_strategy(sid)
                eliminated_ids.append(sid)

        # Phase 3: 精英识别
        elites = []
        for _, row in leaderboard.head(self.elite_count).iterrows():
            sid = row.get("strategy_id")
            if sid in self.arena.strategies:
                elites.append(
                    {
                        "id": sid,
                        "name": row.get("name", sid),
                        "strategy": self.arena.strategies[sid].meta,
                        "performance": row.to_dict(),
                    }
                )
        if not elites:
            logger.warning("无精英策略，取消进化")
            return {"cycle": cycle, "error": "no_elites"}

        # Phase 4: 变异
        mutants = []
        for elite in elites:
            for _ in range(self.mutants_per_elite):
                mutant = self.generator.mutate_strategy(elite["strategy"], elite["performance"])
                if mutant:
                    mutant["parent_strategy_id"] = elite["id"]
                    mutant["generation"] = elite["strategy"].get("generation", 0) + 1
                    mutant["generation_method"] = "mutate"
                    mutants.append(mutant)

        # Phase 5: 杂交
        hybrids = []
        if self.enable_crossover:
            for i in range(len(elites)):
                for j in range(i + 1, len(elites)):
                    hybrid = self.generator.crossover_strategies(
                        elites[i]["strategy"],
                        elites[j]["strategy"],
                        elites[i]["performance"],
                        elites[j]["performance"],
                    )
                    if hybrid:
                        hybrid["generation_method"] = "crossover"
                        hybrid["generation"] = max(
                            elites[i]["strategy"].get("generation", 0),
                            elites[j]["strategy"].get("generation", 0),
                        ) + 1
                        hybrids.append(hybrid)

        # Phase 6: 新生
        try:
            new_strategies = self.generator.generate_new_strategies(
                market_context or {}, n_strategies=self.new_per_cycle
            )
        except Exception as e:
            logger.error(f"新生策略生成失败: {e}")
            new_strategies = []

        # Phase 7: 验证与上线
        all_new = mutants + hybrids + new_strategies
        added = 0
        for strategy_dict in all_new:
            success, msg, func, obj = self.compiler.compile(strategy_dict)
            if not success:
                logger.debug(f"编译失败: {msg}")
                continue
            report = self.validator.validate(func, strategy_dict, verbose=False)
            if not report.passed and not self._is_data_insufficient(report):
                logger.debug(
                    f"验证失败 [{obj.name}]: {'; '.join(report.warnings)}"
                )
                continue
            if self.arena.add_strategy(obj.id, func, strategy_dict):
                self._persist_strategy(obj, strategy_dict)
                added += 1

        # Phase 8: 记录进化日志
        summary = {
            "cycle": cycle,
            "eliminated": n_eliminate,
            "mutated": len(mutants),
            "crossover": len(hybrids),
            "new": len(new_strategies),
            "added": added,
            "arena_size": len(self.arena.strategies),
            "timestamp": datetime.now().isoformat(),
        }
        self._log_evolution(summary)
        logger.info(
            f"进化周期 #{cycle} 完成: 淘汰{n_eliminate}, 变异{len(mutants)}, "
            f"杂交{len(hybrids)}, 新生{len(new_strategies)}, 上线{added}"
        )
        return summary

    def seed_initial_population(self, n: int = 5) -> int:
        """直接注入内置经典策略模板（不调用 LLM），返回上线数量"""
        if len(self.arena.strategies) > 0:
            return 0
        templates = self.generator._builtin_templates()[:n]
        added = 0
        for strategy_dict in templates:
            success, msg, func, obj = self.compiler.compile(strategy_dict)
            if not success:
                continue
            report = self.validator.validate(func, strategy_dict, verbose=False)
            if not report.passed and not self._is_data_insufficient(report):
                continue
            if self.arena.add_strategy(obj.id, func, strategy_dict):
                self._persist_strategy(obj, strategy_dict)
                added += 1
        logger.info(f"初始种群注入完成: {added} 个策略")
        return added

    async def run_simulation_replay(self, days: int = 60) -> int:
        """回放最近 N 个交易日模拟交易，确保面板有历史数据"""
        with get_db_session() as session:
            rows = session.execute(
                text(
                    "SELECT DISTINCT trade_date FROM daily_price "
                    "ORDER BY trade_date DESC LIMIT :n"
                ),
                {"n": days},
            ).fetchall()
        trade_dates = sorted(r[0] for r in rows)
        for d in trade_dates:
            await self.arena.run_daily(d)
        logger.info(f"模拟交易回放完成: {len(trade_dates)} 个交易日")
        return len(trade_dates)

    def _ensure_arena_populated(self):
        """竞技场为空时，从数据库加载持久化策略"""
        if len(self.arena.strategies) > 0:
            return
        with get_db_session() as session:
            result = session.execute(
                text(
                    """
                    SELECT id, meta FROM strategies
                    WHERE status = 'active' ORDER BY created_at
                    """
                )
            )
            rows = result.fetchall()
        loaded = 0
        for sid, meta in rows:
            if self.arena.has_strategy(sid):
                continue
            try:
                meta_dict = meta if isinstance(meta, dict) else json.loads(meta)
            except (TypeError, ValueError):
                continue
            success, msg, func, obj = self.compiler.compile(meta_dict)
            if success:
                self.arena.add_strategy(sid, func, meta_dict)
                loaded += 1
        if loaded == 0:
            logger.warning("数据库无持久化策略，注入内置种子策略")
            self._inject_seed_strategies()

    def _is_data_insufficient(self, report) -> bool:
        """验证失败仅因历史数据不足时放宽（演示阶段无真实数据）"""
        if report.passed:
            return False
        weak_reasons = ("数据不足", "样本量不足", "有效交易天数过少")
        return any(w and any(k in w for k in weak_reasons) for w in report.warnings)

    def _inject_seed_strategies(self, n: int = 5):
        """注入内置经典策略作为起始种群"""
        try:
            strategies = self.generator.generate_new_strategies({}, n_strategies=n)
        except Exception as e:
            logger.error(f"种子策略生成失败: {e}")
            return
        for strategy_dict in strategies:
            success, msg, func, obj = self.compiler.compile(strategy_dict)
            if not success:
                continue
            report = self.validator.validate(func, strategy_dict, verbose=False)
            if not report.passed and not self._is_data_insufficient(report):
                logger.debug(f"种子策略验证未通过 [{obj.name}]: {report.warnings}")
                continue
            if self.arena.add_strategy(obj.id, func, strategy_dict):
                self._persist_strategy(obj, strategy_dict)
                logger.info(f"注入种子策略: {obj.name}")

    def _mark_eliminated(self, strategy_id: str):
        with get_db_session() as session:
            session.execute(
                text(
                    "UPDATE strategies SET status='eliminated', eliminated_at=NOW() WHERE id=:id"
                ),
                {"id": strategy_id},
            )

    def _persist_strategy(self, obj, strategy_dict: dict):
        with get_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO strategies (id, name, type, meta, status, generation, parent_id)
                    VALUES (:id, :name, :type, :meta, :status, :generation, :parent_id)
                    ON CONFLICT (id) DO UPDATE SET
                        meta = EXCLUDED.meta,
                        updated_at = NOW()
                    """
                ),
                {
                    "id": obj.id,
                    "name": obj.name,
                    "type": obj.type.value if hasattr(obj.type, "value") else str(obj.type),
                    "meta": json.dumps(strategy_dict, ensure_ascii=False),
                    "status": "active",
                    "generation": obj.generation,
                    "parent_id": obj.parent_strategy_id,
                },
            )

    def _log_evolution(self, summary: dict):
        with get_db_session() as session:
            session.execute(
                text(
                    """
                    INSERT INTO evolution_log
                        (cycle, eliminated_count, mutated_count, crossover_count,
                         new_count, added_count, arena_size, details)
                    VALUES (:cycle, :eliminated, :mutated, :crossover,
                            :new, :added, :arena_size, :details)
                    """
                ),
                {
                    "cycle": summary["cycle"],
                    "eliminated": summary["eliminated"],
                    "mutated": summary["mutated"],
                    "crossover": summary["crossover"],
                    "new": summary["new"],
                    "added": summary["added"],
                    "arena_size": summary["arena_size"],
                    "details": json.dumps(summary, ensure_ascii=False),
                },
            )
