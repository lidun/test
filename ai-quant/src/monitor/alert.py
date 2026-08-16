"""告警模块：钉钉/邮件通知，含去重"""
from __future__ import annotations

import smtplib
from datetime import datetime
from email.mime.text import MIMEText

import requests
from loguru import logger

from src.core.config import config
from src.monitor.metrics import StrategyAlert


class AlertManager:
    def __init__(self):
        self.dingtalk_webhook = config.alert.dingtalk_webhook
        self.email_to = config.alert.email_alert
        self._alert_cache = {}

    def send_strategy_alert(self, alert: StrategyAlert) -> bool:
        dedup_key = f"{alert.strategy_id}_{alert.alert_type}"
        if dedup_key in self._alert_cache:
            if (datetime.now() - self._alert_cache[dedup_key]).total_seconds() < 1800:
                return True

        message = self._format_message(alert)
        sent = False
        if self.dingtalk_webhook:
            try:
                if self._send_dingtalk(message):
                    sent = True
            except Exception as e:
                logger.error(f"钉钉告警失败: {e}")
        if self.email_to and alert.severity == "critical":
            try:
                if self._send_email(
                    f"[AI量化] {alert.severity}: {alert.strategy_name}", message
                ):
                    sent = True
            except Exception as e:
                logger.error(f"邮件告警失败: {e}")

        if sent:
            self._alert_cache[dedup_key] = datetime.now()
        logger.warning(f"策略告警: {alert.strategy_name} - {alert.message}")
        return sent

    def send_system_alert(self, message: str, severity: str = "warning"):
        payload = {"msgtype": "text", "text": {"content": f"[{severity}] {message}"}}
        if self.dingtalk_webhook:
            try:
                requests.post(self.dingtalk_webhook, json=payload, timeout=10)
            except Exception as e:
                logger.error(f"钉钉系统告警失败: {e}")

    def check_and_alert(self, alerts, system_metrics):
        """批量处理告警（供调度器调用）"""
        for alert in alerts:
            self.send_strategy_alert(alert)
        if system_metrics and not system_metrics.db_ok:
            self.send_system_alert("数据库连接异常", "critical")

    def _format_message(self, alert: StrategyAlert) -> str:
        return f"""
【AI量化策略告警】[{alert.severity.upper()}]
策略: {alert.strategy_name}
类型: {alert.alert_type}
详情: {alert.message}
当前值: {alert.current_value}
阈值: {alert.threshold}
""".strip()

    def _send_dingtalk(self, message: str) -> bool:
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": "AI量化告警", "text": message},
        }
        response = requests.post(self.dingtalk_webhook, json=payload, timeout=10)
        return response.status_code == 200 and response.json().get("errcode") == 0

    def _send_email(self, subject: str, body: str) -> bool:
        # 依赖环境变量 SMTP_*；未配置时静默跳过
        host = _get("SMTP_HOST")
        port = int(_get("SMTP_PORT") or 465)
        user = _get("SMTP_USER")
        password = _get("SMTP_PASSWORD")
        if not host or not user or not password:
            return False
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = self.email_to
        with smtplib.SMTP_SSL(host, port, timeout=10) as server:
            server.login(user, password)
            server.sendmail(user, [self.email_to], msg.as_string())
        return True


def _get(name: str):
    import os

    return os.getenv(name, "")
